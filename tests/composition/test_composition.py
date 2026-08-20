"""Runtime Composition contracts at stable graph boundaries."""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from pydantic_ai.models.test import TestModel

from openbiliclaw.access.models import AccessStatusKind, CredentialAccessHandle, Permission
from openbiliclaw.ai.providers.embeddings import EmbeddingBatch, EmbeddingService
from openbiliclaw.ai.providers.embeddings.index import EmbeddingIndex
from openbiliclaw.ai.providers.models import BuiltModel, ModelInstanceConfig
from openbiliclaw.ai.providers.verification import VerifiedCapabilities
from openbiliclaw.ai.runtime.capabilities import ModelCapabilities
from openbiliclaw.application.refresh_recommendations import RefreshRecommendationsCommand
from openbiliclaw.composition.application import Application, ApplicationServices
from openbiliclaw.composition.build import BuildOptions, build_application
from openbiliclaw.composition.entrypoints import _parser, main
from openbiliclaw.composition.lifecycle import ComponentStage, LifecyclePlan, RuntimeComponent
from openbiliclaw.composition.providers import (
    _UnavailableCredentialTransport,
    _UnavailableIdentityClient,
    _UnavailableProbe,
    _VaultCredentialResolver,
)
from openbiliclaw.composition.reload import ApplicationReference, reload_application
from openbiliclaw.core.config import AppSettings
from openbiliclaw.core.health import HealthStatus
from openbiliclaw.core.jobs import JobDecision
from openbiliclaw.hosts.api.dependencies import DiagnosticResult, StartResult
from openbiliclaw.infrastructure.credentials.keyring import ProtectedFileBackend
from openbiliclaw.infrastructure.credentials.vault import CredentialVault
from openbiliclaw.observations.models import ContentOpenedObservation, HostOpenPayload
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.understanding.evidence import EvidenceLink
from openbiliclaw.understanding.profile import StableInterestClaim, claim_id
from openbiliclaw.understanding.proposals import ClaimProposal, ProposalOwner


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


def test_composition_constructs_configured_embedding_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Transport:
        async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
            return EmbeddingBatch(vectors=tuple((1.0,) for _ in texts), input_tokens=0)

    monkeypatch.setattr(
        "openbiliclaw.composition.build.build_embedding_transport",
        lambda *_args, **_kwargs: Transport(),
    )
    application = build_application(
        AppSettings(
            embedding={
                "model_name": "BAAI/bge-small-zh-v1.5",
                "endpoint": "http://127.0.0.1:7997",
                "secret_ref": "vault:cred_" + "a" * 32,
                "output_dimensions": 512,
            }
        ),
        options=BuildOptions(data_dir=tmp_path),
    )

    assert isinstance(application.services.embeddings, EmbeddingService)


@pytest.mark.asyncio
async def test_composed_understanding_embedding_outage_is_fail_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, str]] = []

    async def outage(_self: EmbeddingIndex, kind: str, ref_id: str, _text: str) -> bool:
        calls.append((kind, ref_id))
        raise RuntimeError("embedding provider down")

    monkeypatch.setattr(EmbeddingIndex, "upsert", outage)
    application = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    await application.start()
    try:
        assert application.repositories is not None
        assert application.services.understanding is not None
        now = datetime.now(UTC)
        event = ContentOpenedObservation(
            observation_id="obs_" + "8" * 32,
            idempotency_key="embedding-outage-source",
            occurred_at=now,
            received_at=now,
            content_ref=None,
            provenance=ObservationProvenance(
                producer_id="host.web",
                source=ObservationSource.HOST,
                authenticated=True,
                trust_level=TrustLevel.HIGH,
            ),
            payload=HostOpenPayload(surface="web"),
        )
        await application.repositories.observations.insert_batch((event,))
        evidence = EvidenceLink(
            evidence_id="ev_" + "8" * 32,
            observation_id=event.observation_id,
            summary="User explicitly likes science",
            occurred_at=now,
            trust=1.0,
        )
        claim = StableInterestClaim(
            claim_id=claim_id("stable_interest", "science"),
            value="science",
            confidence=0.9,
            fresh_at=now,
            evidence_ids=(evidence.evidence_id,),
        )
        decision = await application.services.understanding.consider(
            "default",
            ClaimProposal(
                proposal_id="prop_" + "8" * 32,
                analyzer_id="understanding.preference.v1",
                owner=ProposalOwner.PREFERENCE,
                claim=claim,
                evidence=(evidence,),
                proposed_at=now,
            ),
            evidence,
        )

        assert decision.reason == "accepted"
        assert {kind for kind, _ref_id in calls} == {"evidence", "claim"}
        assert (await application.services.understanding.profile("default")).claims == (claim,)
    finally:
        await application.stop()


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
async def test_reload_drain_validation_and_timeout_cleanup() -> None:
    events: list[str] = []
    old = _application("old", events)
    await old.start()
    reference = ApplicationReference(old)
    with pytest.raises(ValueError, match="positive"):
        await reference.drain(old, 0)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        async with reference.lease():
            entered.set()
            await release.wait()

    task = asyncio.create_task(hold())
    await entered.wait()
    assert not await reference.drain(old, 0.001)
    release.set()
    await task
    assert await reference.drain(old, 1)
    await old.stop()


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
    assert app.providers is not None
    assert app.providers.enabled == ("v2ex",)
    assert app.providers.degraded == ("unknown",)
    await app.start()
    assert (tmp_path / "openbiliclaw.db").exists()
    assert app.lifecycle.health().status is HealthStatus.DEGRADED
    await app.stop()
    assert app.resources is not None
    assert app.resources.database.closed
    assert app.resources.http.open_client_count == 0
    assert app.resources.events.subscriber_count == 0


@pytest.mark.asyncio
async def test_composition_start_rehydrates_preseeded_bilibili_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credentials_path = tmp_path / "credentials.json"
    vault = CredentialVault(ProtectedFileBackend(credentials_path))
    reference = vault.stable_reference("builtin.manual:bilibili:account:none")
    vault.put(reference, b'{"cookie":"SESSDATA=session-value; bili_jct=csrf-value"}')

    class Transport:
        async def __call__(
            self,
            method: str,
            path: str,
            query: str,
            cookie: str | None,
            body: bytes,
        ) -> bytes:
            del method, query, body
            assert path == "/x/web-interface/nav"
            assert cookie == "SESSDATA=session-value; bili_jct=csrf-value"
            return b'{"code":0,"data":{"isLogin":true,"mid":1,"uname":"tester"}}'

    monkeypatch.setattr(
        "openbiliclaw.composition.build.keyring_or_file",
        lambda path: ProtectedFileBackend(path),
    )
    monkeypatch.setattr("openbiliclaw.composition.providers.HttpxBilibiliTransport", Transport)
    app = build_application(
        AppSettings(content={"enabled": ("bilibili",)}),
        options=BuildOptions(data_dir=tmp_path),
    )
    await app.start()
    try:
        facade = app.services.facade
        assert facade is not None
        result = await facade.source_status("bilibili", None)
        assert result.status.state is AccessStatusKind.CONNECTED
        assert result.status.method_id == "builtin.manual"
    finally:
        await app.stop()


def test_composed_model_configuration_uses_runtime_paths(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[model]\nmodel_name = ""\n', encoding="utf-8")
    app = build_application(
        AppSettings(), options=BuildOptions(data_dir=tmp_path, config_path=config)
    )
    dependencies = app.hosts.dependencies
    assert dependencies is not None and dependencies.models is not None
    assert dependencies.models.settings() == app.settings


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
async def test_composed_host_accepts_realistic_web_and_extension_origins(tmp_path: Path) -> None:
    app = build_application(AppSettings(), options=BuildOptions(data_dir=tmp_path))
    assert app.hosts.api is not None
    transport = httpx.ASGITransport(app=app.hosts.api)
    headers = {"X-Device-ID": "device", "X-CSRF-Token": "device"}
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        app.hosts.api.router.lifespan_context(app.hosts.api),
    ):
        web = await client.post(
            "/v1/recommendations/refresh",
            json={"idempotency_key": "origin-web", "maximum_items": 1},
            headers={**headers, "Origin": "http://127.0.0.1:8420"},
        )
        extension = await client.post(
            "/v1/recommendations/refresh",
            json={"idempotency_key": "origin-extension", "maximum_items": 1},
            headers={**headers, "Origin": "chrome-extension://abc123"},
        )
    assert web.status_code == extension.status_code == 200


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
        facade.assistant_turn(object(), "device"),  # type: ignore[arg-type]
        facade.conversation("conv", "device"),
        facade.conversation_messages("conv", "device", 10),
    ):
        with pytest.raises(Exception, match="not configured"):
            await operation
    await app.start()
    try:
        health = (await facade.job_health()).health
        assert health.status is HealthStatus.HEALTHY
        assert health.component_id == "runtime.supervisor"
        refreshed = await facade.refresh_recommendations(
            RefreshRecommendationsCommand(idempotency_key="bounded-refresh-test", maximum_items=1)
        )
        assert refreshed.decision is JobDecision.RUN
        await asyncio.sleep(0)
        refreshed_health = (await facade.job_health()).health
        assert refreshed_health.jobs[0].job_id == "recommendation.replenishment"
    finally:
        await app.stop()


def test_frontend_environment_path_rebuilds_only_the_api_host(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("docker-web", encoding="utf-8")
    application = build_application(AppSettings())
    replaced = application.with_api_frontend(tmp_path)
    assert replaced.lifecycle is application.lifecycle
    assert replaced.hosts.api is not application.hosts.api


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


def test_non_loopback_host_resolves_bearer_from_vault(tmp_path: Path) -> None:
    from openbiliclaw.infrastructure.credentials.keyring import ProtectedFileBackend
    from openbiliclaw.infrastructure.credentials.vault import CredentialVault

    secret_id = CredentialVault(ProtectedFileBackend(tmp_path / "credentials.json")).store(
        b"test-bearer"
    )
    application = build_application(
        AppSettings(
            host={
                "api_host": "0.0.0.0",
                "bearer_secret_ref": f"vault:{secret_id}",
            }
        ),
        options=BuildOptions(data_dir=tmp_path),
    )
    assert application.hosts.api is not None
    override = next(iter(application.hosts.api.dependency_overrides.values()))
    dependencies = override()
    assert dependencies.security.bearer_token == "test-bearer"
    assert "test-bearer" not in repr(dependencies.security)


def test_model_configuration_wires_assistant_and_understanding_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = ModelInstanceConfig(
        provider="openai",
        protocol="openai",
        endpoint="https://gateway.example/v1",
        model_name="test",
        secret_ref="cred_" + "a" * 32,
        capabilities=ModelCapabilities(
            tools=True, structured_output=True, context_tokens=8192, streaming=True
        ),
    )
    built_configs: list[ModelInstanceConfig] = []

    def build_model(_factory: object, model_config: ModelInstanceConfig) -> BuiltModel:
        built_configs.append(model_config)
        return BuiltModel(
            model=TestModel(),
            instance_id="test:model",
            provider="openai",
            owner="assistant",
            declared_capabilities=config.capabilities,
            verification=VerifiedCapabilities.unverified(config),
        )

    monkeypatch.setattr("openbiliclaw.composition.build.ModelFactory.build", build_model)
    (tmp_path / "models.dev.json").write_bytes(
        (Path(__file__).parents[1] / "fixtures" / "models.dev.small.json").read_bytes()
    )
    app = build_application(
        AppSettings(
            model={
                "provider": "openai",
                "endpoint": "https://gateway.example/v1",
                "model_name": "gpt-4o-mini",
                "secret_ref": "vault:cred_" + "a" * 32,
                "options": {"disable_thinking": True},
            }
        ),
        options=BuildOptions(data_dir=tmp_path),
    )
    assert app.services.assistant is not None
    assert built_configs[0].options.disable_thinking is True
    jobs_component = next(
        item for item in app.lifecycle._configured if item.component_id == "core.jobs"
    )
    component = cast("Any", jobs_component.component)
    assert "understanding.analysis" in component._scheduler._jobs


@pytest.mark.asyncio
async def test_fail_closed_provider_adapters_raise_without_fake_transport() -> None:
    transport = _UnavailableCredentialTransport()
    with pytest.raises(RuntimeError, match="not configured"):
        await transport.search("q", None, 1, None)
    with pytest.raises(RuntimeError, match="not configured"):
        await transport.fetch("1", None)
    with pytest.raises(RuntimeError, match="not configured"):
        await _UnavailableProbe()("credential")
    with pytest.raises(RuntimeError, match="unavailable"):
        await _UnavailableIdentityClient().identity("token")

    handle = CredentialAccessHandle(
        provider_id="demo",
        account_id="local",
        permissions=frozenset({Permission.READ_PRIVATE}),
        credential_ref="cred_" + "a" * 32,
        revision=1,
    )

    class Vault:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def resolve(self, _identity: str, callback):  # type: ignore[no-untyped-def]
            return callback(memoryview(self.payload))

    assert (
        await _VaultCredentialResolver(cast("Any", Vault(b'{"cookie":"safe"}')))(handle) == "safe"
    )
    for payload in (b"[]", b'{"one":"x","two":"y"}', b'{"cookie":1}'):
        with pytest.raises(ValueError, match="credential"):
            await _VaultCredentialResolver(cast("Any", Vault(payload)))(handle)


@pytest.mark.asyncio
async def test_youtube_connects_anonymously_without_credentials(tmp_path: Path) -> None:
    app = build_application(
        AppSettings(),
        options=BuildOptions(data_dir=tmp_path, enabled_providers=("youtube",)),
    )
    assert app.hosts.api is not None
    await app.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app.hosts.api), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/sources/connect",
                json={
                    "provider_id": "youtube",
                    "method_id": "builtin.anonymous",
                    "idempotency_key": "connect:youtube:anonymous",
                },
                headers={"X-Device-ID": "test", "X-CSRF-Token": "test"},
            )
        assert response.status_code == 200
        assert response.json()["status"]["state"] == "connected"
        assert response.json()["status"]["method_id"] == "builtin.anonymous"
    finally:
        await app.stop()


def test_all_explicit_first_party_provider_builders_validate() -> None:
    app = build_application(
        AppSettings(),
        options=BuildOptions(
            enabled_providers=(
                "bangumi",
                "bilibili",
                "douyin",
                "hackernews",
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
        "hackernews",
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


@pytest.mark.parametrize(
    "arguments",
    (
        ("--config", "settings.toml", "--data-dir", "state", "serve"),
        ("serve", "--config", "settings.toml", "--data-dir", "state"),
    ),
)
def test_global_cli_options_work_before_or_after_command(arguments: tuple[str, ...]) -> None:
    parsed = _parser().parse_args(arguments)
    assert parsed.config == Path("settings.toml")
    assert parsed.data_dir == Path("state")


@pytest.mark.parametrize(
    ("arguments", "description"),
    (
        (("check",), "Validate configuration"),
        (("serve",), "Serve the local web"),
        (("set-password",), "Configure the web login"),
        (("ext-token",), "Mint a durable"),
        (("export",), "ZIP archive regardless"),
        (("import",), "Import a backup"),
        (("sources",), "List, inspect, connect"),
        (("sources", "list"), "List source connection"),
        (("sources", "status"), "Show one source"),
        (("sources", "form"), "Show a source connection form"),
        (("sources", "capabilities"), "Show provider capabilities"),
        (("sources", "access-recipe"), "Show the provider access recipe"),
        (("sources", "submit-material"), "Submit plugin-captured access material"),
        (("sources", "add"), "Connect a source"),
        (("sources", "remove"), "Disconnect a source"),
        (("sources", "sync"), "Synchronize evidence"),
        (("feed",), "Show the current recommendation"),
        (("refresh",), "Request a bounded recommendation refresh"),
        (("feedback",), "Record explicit feedback"),
        (("record-feedback",), "Record a complete typed feedback request"),
        (("observations",), "Record a typed observation batch"),
        (("profile",), "Inspect the profile"),
        (("profile", "show"), "Show the bounded preference"),
        (("profile", "exploration"), "Enable or disable"),
        (("profile", "edit"), "Apply a complete typed profile edit"),
        (("assistant",), "Send one message"),
        (("conversations",), "Inspect Assistant conversations"),
        (("conversations", "show"), "Show one Assistant conversation"),
        (("conversations", "messages"), "Show messages in one Assistant conversation"),
        (("search",), "Search content"),
        (("content",), "Inspect content details"),
        (("content", "detail"), "Fetch provider-native content details"),
        (("actions",), "Propose, confirm, or reject"),
        (("actions", "propose"), "Propose a pending content action"),
        (("actions", "confirm"), "Confirm a pending content action"),
        (("actions", "reject"), "Reject a pending content action"),
        (("runtime",), "Inspect runtime health"),
        (("runtime", "health"), "Show supervised runtime health"),
        (("runtime", "config-diagnostics"), "Show configuration diagnostics"),
        (("runtime", "model-diagnostics"), "Show model diagnostics"),
        (("runtime", "events"), "Replay bounded runtime events"),
        (("models",), "Inspect or update model settings"),
        (("models", "catalog"), "Show the supported model catalog"),
        (("models", "current"), "Show current model settings"),
        (("models", "set"), "Validate and persist model settings"),
    ),
)
def test_cli_help_describes_every_command(
    arguments: tuple[str, ...], description: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit, match="0"):
        _parser().parse_args((*arguments, "--help"))
    assert description in capsys.readouterr().out


def test_check_entrypoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["openbiliclaw", "check", "--data-dir", str(tmp_path)])
    main()
    assert (tmp_path / "openbiliclaw.db").exists()
    assert json.loads(capsys.readouterr().out) == {"ready": True}


def test_missing_config_reports_clean_cli_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.toml"
    monkeypatch.setattr("sys.argv", ["openbiliclaw", "--config", str(missing), "check"])

    with pytest.raises(SystemExit, match="2"):
        main()

    captured = capsys.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)["error"]
    assert error["code"] == "validation"
    assert str(missing) in error["message"]


def test_serve_without_frontend_environment_uses_composed_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[object] = []
    monkeypatch.delenv("OPENBILICLAW_FRONTEND_DIR", raising=False)
    monkeypatch.setattr("sys.argv", ["openbiliclaw", "serve", "--data-dir", str(tmp_path)])
    monkeypatch.setattr(
        "openbiliclaw.composition.entrypoints.uvicorn.run",
        lambda app, **_options: calls.append(app),
    )
    main()
    assert len(calls) == 1


def test_serve_uses_composed_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[object] = []
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("docker frontend", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_FRONTEND_DIR", str(frontend))
    monkeypatch.setattr("sys.argv", ["openbiliclaw", "serve", "--data-dir", str(tmp_path)])
    monkeypatch.setattr(
        "openbiliclaw.composition.entrypoints.uvicorn.run",
        lambda app, **_options: calls.append(app),
    )
    main()
    assert len(calls) == 1
