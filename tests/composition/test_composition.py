"""Runtime Composition contracts at stable graph boundaries."""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from openbiliclaw.composition.application import Application, ApplicationServices
from openbiliclaw.composition.build import BuildOptions, build_application
from openbiliclaw.composition.entrypoints import main
from openbiliclaw.composition.lifecycle import ComponentStage, LifecyclePlan, RuntimeComponent
from openbiliclaw.composition.reload import ApplicationReference, reload_application
from openbiliclaw.core.config import AppSettings
from openbiliclaw.core.health import HealthStatus
from openbiliclaw.hosts.api.dependencies import DiagnosticResult, StartResult
from openbiliclaw.observations.models import ContentOpenedObservation, HostOpenPayload
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)


class _Component:
    def __init__(self, name: str, events: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail = fail
        self.running = False

    async def start(self) -> None:
        self.events.append(f"start:{self.name}")
        self.running = True
        if self.fail:
            raise RuntimeError(self.name)

    async def stop(self) -> None:
        self.events.append(f"stop:{self.name}")
        self.running = False

    async def ready(self) -> bool:
        return self.running


def _application(name: str, events: list[str], *, fail: bool = False) -> Application:
    component = _Component(name, events, fail=fail)
    lifecycle = LifecyclePlan((RuntimeComponent(name, ComponentStage.INFRASTRUCTURE, component),))
    return Application(
        settings=AppSettings(),
        services=ApplicationServices(),
        lifecycle=lifecycle,
    )


@pytest.mark.asyncio
async def test_lifecycle_orders_start_and_reverse_shutdown_and_rolls_back() -> None:
    events: list[str] = []
    components = tuple(
        RuntimeComponent(name, stage, _Component(name, events, fail=name == "service"))
        for name, stage in (
            ("database", ComponentStage.INFRASTRUCTURE),
            ("service", ComponentStage.SERVICE),
            ("jobs", ComponentStage.CORE_JOBS),
            ("host", ComponentStage.HOST),
        )
    )
    lifecycle = LifecyclePlan(components)

    with pytest.raises(RuntimeError, match="service"):
        await lifecycle.start()

    assert events == ["start:database", "start:service", "stop:service", "stop:database"]
    assert lifecycle.health().status is HealthStatus.STOPPED


@pytest.mark.asyncio
async def test_atomic_reload_swaps_ready_candidate_then_drains_old() -> None:
    events: list[str] = []
    active = _application("old", events)
    await active.start()
    reference = ApplicationReference(active)

    replacement = await reload_application(
        reference,
        AppSettings(host={"api_port": 8430}),
        builder=lambda settings: _application(f"new:{settings.host.api_port}", events),
        drain_timeout_seconds=1,
    )

    assert replacement
    assert reference.current.settings.host.api_port == 8420  # test builder owns its settings
    assert events == ["start:old", "start:new:8430", "stop:old"]
    await reference.current.stop()


@pytest.mark.asyncio
async def test_failed_reload_keeps_active_and_closes_candidate() -> None:
    events: list[str] = []
    active = _application("old", events)
    await active.start()
    reference = ApplicationReference(active)

    replaced = await reload_application(
        reference,
        AppSettings(),
        builder=lambda _settings: _application("bad", events, fail=True),
        drain_timeout_seconds=1,
    )

    assert not replaced
    assert reference.current is active
    assert events == ["start:old", "start:bad", "stop:bad"]
    await active.stop()


@pytest.mark.asyncio
async def test_cancelled_reload_closes_candidate_and_propagates() -> None:
    events: list[str] = []
    active = _application("old", events)
    await active.start()
    reference = ApplicationReference(active)
    candidate = _application("candidate", events)

    task = asyncio.create_task(
        reload_application(
            reference,
            AppSettings(),
            builder=lambda _settings: candidate,
            drain_timeout_seconds=1,
        )
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reference.current is active
    assert not candidate.lifecycle.active_component_ids
    await active.stop()


@pytest.mark.asyncio
async def test_reload_does_not_interrupt_inflight_request() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    events: list[str] = []
    old = _application("old", events)
    await old.start()
    reference = ApplicationReference(old)

    async def request() -> Application:
        async with reference.lease() as leased:
            entered.set()
            await release.wait()
            return leased

    task = asyncio.create_task(request())
    await entered.wait()
    replacement = asyncio.create_task(
        reload_application(
            reference,
            AppSettings(),
            builder=lambda _settings: _application("new", events),
            drain_timeout_seconds=1,
        )
    )
    await asyncio.sleep(0)
    assert reference.current is not old
    assert "stop:old" not in events
    release.set()
    assert await task is old
    assert await replacement
    await reference.current.stop()


@pytest.mark.asyncio
async def test_production_graph_is_lazy_and_leak_free(tmp_path: Path) -> None:
    options = BuildOptions(data_dir=tmp_path, enabled_providers=("v2ex", "unknown"))
    app = build_application(AppSettings(), options=options)

    assert not (tmp_path / "openbiliclaw.db").exists()
    assert app.providers.enabled == ("v2ex",)
    assert app.providers.degraded == ("unknown",)
    await app.start()
    assert (tmp_path / "openbiliclaw.db").exists()
    assert app.lifecycle.health().status is HealthStatus.DEGRADED
    await app.stop()
    assert app.resources.database.closed
    assert app.resources.http.open_client_count == 0
    assert app.resources.events.subscriber_count == 0


@pytest.mark.asyncio
async def test_fresh_composed_host_records_and_reads_without_credentials(tmp_path: Path) -> None:
    app = build_application(
        AppSettings(content={"enabled": ("v2ex",)}),
        options=BuildOptions(data_dir=tmp_path),
    )
    assert app.hosts.api is not None
    now = datetime.now(UTC)
    observation = ContentOpenedObservation(
        observation_id="obs_" + "1" * 32,
        idempotency_key="composition:e2e:1",
        occurred_at=now,
        received_at=now,
        content_ref=None,
        provenance=ObservationProvenance(
            producer_id="host.web",
            source=ObservationSource.HOST,
            authenticated=False,
            trust_level=TrustLevel.LOW,
        ),
        payload=HostOpenPayload(surface="web"),
    )
    transport = httpx.ASGITransport(app=app.hosts.api)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        app.hosts.api.router.lifespan_context(app.hosts.api),
    ):
        health = await client.get("/v1/runtime/health")
        sources = await client.get("/v1/sources")
        recommendations = await client.get("/v1/recommendations")
        recorded = await client.post(
            "/v1/observations",
            json={
                "idempotency_key": "composition:batch:1",
                "observations": [observation.model_dump(mode="json")],
                "allowed_event_types": ["content_opened"],
            },
            headers={"X-Device-ID": "device", "X-CSRF-Token": "device"},
        )
    assert health.status_code == sources.status_code == recommendations.status_code == 200
    assert recorded.status_code == 200, recorded.text
    assert app.resources is not None and app.resources.database.closed


@pytest.mark.asyncio
async def test_facade_diagnostics_and_optional_capabilities_fail_closed(tmp_path: Path) -> None:
    app = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    facade = app.services.facade
    assert facade is not None
    assert await facade.config_diagnostics() == DiagnosticResult(
        healthy=True, detail="configuration validated"
    )
    assert not (await facade.model_diagnostics()).healthy
    assert await facade.start() == StartResult(started=True)
    for operation in (
        facade.assistant_turn(None, "device"),
        facade.conversation("conv", "device"),
        facade.conversation_messages("conv", "device", 10),
    ):
        with pytest.raises(Exception, match="not configured"):
            await operation
    await app.start()
    try:
        assert (await facade.job_health()).health.status is HealthStatus.HEALTHY
    finally:
        await app.stop()


def test_product_modules_never_import_composition() -> None:
    root = Path("src/openbiliclaw")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "composition" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "openbiliclaw.composition"
            ):
                offenders.append(str(path))
            if isinstance(node, ast.Import) and any(
                alias.name.startswith("openbiliclaw.composition") for alias in node.names
            ):
                offenders.append(str(path))
    assert not offenders


def test_all_explicit_first_party_provider_builders_validate() -> None:
    app = build_application(
        AppSettings(),
        options=BuildOptions(
            enabled_providers=(
                "bangumi",
                "bilibili",
                "douyin",
                "linuxdo",
                "reddit",
                "rednote",
                "v2ex",
                "weibo",
                "x",
                "youtube",
                "zhihu",
                "v2ex",
            )
        ),
    )
    assert app.providers is not None
    graph = app.providers
    assert graph.enabled == (
        "bangumi",
        "bilibili",
        "douyin",
        "linuxdo",
        "reddit",
        "rednote",
        "v2ex",
        "weibo",
        "x",
        "youtube",
        "zhihu",
    )
    assert graph.degraded == ()
    assert tuple(item.provider_id.value for item in graph.registry.manifests()) == graph.enabled


def test_check_entrypoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.argv", ["openbiliclaw", "check", "--data-dir", str(tmp_path)])
    main()
    assert (tmp_path / "openbiliclaw.db").exists()


def test_serve_uses_composed_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[object] = []
    monkeypatch.setattr("sys.argv", ["openbiliclaw", "serve", "--data-dir", str(tmp_path)])
    monkeypatch.setattr(
        "openbiliclaw.composition.entrypoints.uvicorn.run",
        lambda app, **_options: calls.append(app),
    )
    main()
    assert len(calls) == 1
