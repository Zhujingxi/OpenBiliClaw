from __future__ import annotations

from collections.abc import AsyncIterator  # noqa: TC003 - async fake protocol clarity
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 - pytest resolves fixture annotations
from threading import Event, Thread
from types import SimpleNamespace
from uuid import UUID, uuid4

import anyio
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.dependencies import (
    AccessPolicy,
    ApplicationContainer,
    DependencyUnavailableError,
)
from openbiliclaw.api.routers.chat import ChatRequest, _chat_events
from openbiliclaw.api.routers.jobs import _job_events
from openbiliclaw.api.routers.onboarding import _progress_events
from openbiliclaw.api.routers.source_tasks import claim_source_task
from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind, ProfileSignal
from openbiliclaw.features.chat.service import ChatChunk, ChatChunkKind
from openbiliclaw.features.library.domain import CollectionKind  # noqa: TC001
from openbiliclaw.features.profile.domain import ProfileSnapshot
from openbiliclaw.features.sources.domain import SourceId, SourceTaskCompletion
from openbiliclaw.features.system.domain import UserSettings
from openbiliclaw.features.system.service import OnboardingWorkflowProgress
from openbiliclaw.infrastructure.ai.health import AIHealthResult, AliasHealth
from openbiliclaw.infrastructure.jobs.tasks import JobRunSnapshot, JobRunStatus


class _Settings:
    value = UserSettings()

    def get(self) -> UserSettings:
        return self.value

    def update(self, patch: dict[str, object]) -> UserSettings:
        merged = self.value.model_dump()
        for field_name in ("source_enabled", "source_weights"):
            partial = patch.get(field_name)
            if isinstance(partial, dict):
                patch = {**patch, field_name: {**merged[field_name], **partial}}
        self.value = UserSettings.model_validate({**merged, **patch})
        return self.value


class _Activity:
    def ingest(self, event: ActivityEvent) -> tuple[ProfileSignal, ...]:
        return (
            ProfileSignal(
                facet="interests",
                value="typed APIs",
                weight=0.5,
                confidence=0.8,
                evidence_ids=(event.id,),
            ),
        )


class _Profile:
    snapshot = ProfileSnapshot(revision=2, narrative="evidence profile")

    def current(self) -> ProfileSnapshot | None:
        return self.snapshot


class _Feed:
    def list_entries(self, *, limit: int = 50, offset: int = 0) -> tuple[object, ...]:
        return ()


class _Feedback:
    def record(self, interaction: object) -> ProfileSignal:
        return ProfileSignal(
            facet="interests",
            value="positive feedback",
            weight=0.5,
            confidence=0.8,
            evidence_ids=(interaction.id,),  # type: ignore[attr-defined]
        )


class _Library:
    def list(self, collection: CollectionKind) -> tuple[object, ...]:
        return ()

    def save(self, collection: CollectionKind, content_id: UUID, *, note: str = "") -> object:
        return SimpleNamespace(
            model_dump=lambda mode=None: {
                "id": str(uuid4()),
                "collection": collection.value,
                "content_id": str(content_id),
                "note": note,
            }
        )

    def remove(self, collection: CollectionKind, content_id: UUID) -> bool:
        return True


class _Chat:
    async def stream(
        self, *, conversation_id: UUID, message: str, learn: bool = False
    ) -> AsyncIterator[ChatChunk]:
        turn_id = uuid4()
        yield ChatChunk(kind=ChatChunkKind.DELTA, content=f"reply:{message}", turn_id=turn_id)
        yield ChatChunk(kind=ChatChunkKind.DONE, content="", turn_id=turn_id)


class _Jobs:
    def __init__(self) -> None:
        self.last_priority: int | None = None
        self.run = JobRunSnapshot(
            id=uuid4(),
            job_name="source_sync",
            idempotency_key="source_sync:test",
            status=JobRunStatus.PENDING,
            priority=100,
            progress=0,
        )

    def schedule(self, job_name: str, *, idempotency_key: str, priority: int | None = None):
        self.last_priority = priority
        return self.run.model_copy(update={"job_name": job_name})

    def inspect(self, run_id: UUID):
        assert run_id == self.run.id
        return self.run.model_copy(update={"status": JobRunStatus.SUCCEEDED, "progress": 1})

    def list(self, *, limit: int = 100):
        return (self.run,)

    def cancel(self, run_id: UUID):
        return self.run.model_copy(update={"status": JobRunStatus.CANCELLED})


class _Onboarding:
    def __init__(self, settings: _Settings, jobs: _Jobs) -> None:
        self._settings = settings
        self._jobs = jobs

    def status(self) -> UserSettings:
        return self._settings.get()

    def start(self, source_ids: tuple[str, ...]):
        return self._jobs.schedule(
            "source_sync", idempotency_key=f"onboarding:{','.join(source_ids)}"
        )

    def progress(self, root_run_id: object):
        run = self._jobs.inspect(root_run_id).model_copy(
            update={
                "job_name": "feed_replenishment",
                "idempotency_key": "feed_replenishment:onboarding:bilibili",
            }
        )
        return OnboardingWorkflowProgress(
            root_run_id=root_run_id,
            stage="feed_replenishment",
            run=run,
            onboarding_complete=True,
        )


class _SourceTasks:
    def claim(self, source_id: str):
        return None

    def complete(self, task_id: UUID, lease_token: str, result: dict[str, object]):
        return SourceTaskCompletion(
            id=task_id,
            completed_at=datetime(2026, 7, 17, tzinfo=UTC),
            idempotent=False,
        )


class _Sources:
    def manifests(self):
        return (
            {
                "source_id": "bilibili",
                "display_name": "Bilibili",
                "capabilities": ["bootstrap_import"],
                "operations": [
                    {
                        "operation": "bootstrap_import",
                        "capability": "bootstrap_import",
                        "result_kind": "activity",
                        "requires_auth": False,
                        "transport_kind": "direct",
                    }
                ],
            },
        )

    def statuses(self):
        return (
            {
                "source_id": "bilibili",
                "account_key": "primary",
                "configured": False,
                "enabled": False,
            },
        )

    def configure(self, source_id: SourceId, account_key: str, credentials: dict[str, object]):
        assert credentials
        return {
            "source_id": source_id.value,
            "account_key": account_key,
            "configured": True,
            "enabled": True,
        }


class _Health:
    async def check_aliases(self) -> AIHealthResult:
        return AIHealthResult(
            proxy_reachable=True,
            aliases=tuple(
                AliasHealth(alias=alias, available=True, state="healthy")
                for alias in ("obc-interactive", "obc-analysis", "obc-embedding")
            ),
        )


def _container(*, token: str = "test-only-access-token") -> ApplicationContainer:
    settings = _Settings()
    jobs = _Jobs()
    return ApplicationContainer(
        access=AccessPolicy(token=token),
        settings=settings,
        onboarding=_Onboarding(settings, jobs),
        sources=_Sources(),
        source_tasks=_SourceTasks(),
        activity=_Activity(),
        profile=_Profile(),
        feed=_Feed(),
        feedback=_Feedback(),
        library=_Library(),
        chat=_Chat(),
        jobs=jobs,
        ai_health=_Health(),
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(container=_container()))


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-only-access-token"}


def test_readiness_is_public_but_every_other_feature_requires_bearer(client: TestClient) -> None:
    assert client.get("/api/v1/system/readiness").status_code == 200
    assert client.get("/api/v1/settings").status_code == 401
    assert (
        client.get("/api/v1/settings", headers={"Authorization": "Basic nope"}).status_code == 401
    )
    assert (
        client.get("/api/v1/settings", headers={"Authorization": "Bearer wrong-token"}).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/settings",
            headers={"Authorization": "bearer test-only-access-token"},
        ).status_code
        == 200
    )
    assert client.get("/api/v1/settings", headers=_auth()).status_code == 200


def test_router_groups_and_representative_happy_paths(client: TestClient) -> None:
    headers = _auth()
    assert (
        client.patch("/api/v1/settings", headers=headers, json={"feed_low_watermark": 5}).json()[
            "feed_low_watermark"
        ]
        == 5
    )
    assert client.get("/api/v1/sources", headers=headers).status_code == 200
    assert client.get("/api/v1/sources/status", headers=headers).status_code == 200
    configured = client.put(
        "/api/v1/sources/bilibili/accounts",
        headers=headers,
        json={"account_key": "primary", "credentials": {"cookie": "opaque-value"}},
    )
    assert configured.status_code == 200
    assert "opaque-value" not in configured.text
    assert "credentials" not in configured.text
    assert (
        client.post(
            "/api/v1/events",
            headers=headers,
            json=ActivityEvent(source_id="bilibili", kind=ActivityKind.VIEW, title="x").model_dump(
                mode="json"
            ),
        ).status_code
        == 202
    )
    assert client.get("/api/v1/profile", headers=headers).json()["revision"] == 2
    assert client.get("/api/v1/feed", headers=headers).status_code == 200
    assert (
        client.post(
            "/api/v1/interactions",
            headers=headers,
            json={"content_id": str(uuid4()), "kind": "positive"},
        ).status_code
        == 201
    )
    assert client.get("/api/v1/library/favorites", headers=headers).status_code == 200
    assert client.get("/api/v1/jobs", headers=headers).status_code == 200
    health = client.get("/api/v1/system/ai-health", headers=headers).json()
    assert [item["alias"] for item in health["aliases"]] == [
        "obc-interactive",
        "obc-analysis",
        "obc-embedding",
    ]


@pytest.mark.parametrize(
    ("route_name", "method", "path", "payload"),
    (
        (
            "events",
            "POST",
            "/api/v1/events",
            ActivityEvent(source_id="bilibili", kind=ActivityKind.VIEW, title="x").model_dump(
                mode="json"
            ),
        ),
        ("feed", "GET", "/api/v1/feed", None),
        (
            "interactions",
            "POST",
            "/api/v1/interactions",
            {"content_id": str(uuid4()), "kind": "positive"},
        ),
        ("sources", "GET", "/api/v1/sources", None),
        ("source_status", "GET", "/api/v1/sources/status", None),
        ("library_list", "GET", "/api/v1/library/favorites", None),
        (
            "library_add",
            "POST",
            "/api/v1/library/favorites",
            {"content_id": str(uuid4())},
        ),
    ),
)
def test_json_routes_reject_malformed_service_output(
    route_name: str,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    container = _container()
    malformed = ({"not": "the documented contract"},)
    if route_name == "events":
        container.activity.ingest = lambda _event: malformed  # type: ignore[method-assign]
    elif route_name == "feed":
        container.feed.list_entries = lambda **_kwargs: malformed  # type: ignore[method-assign]
    elif route_name == "interactions":
        container.feedback.record = lambda _interaction: malformed[0]  # type: ignore[method-assign]
    elif route_name == "sources":
        container.sources.manifests = lambda: malformed  # type: ignore[method-assign]
    elif route_name == "source_status":
        container.sources.statuses = lambda: malformed  # type: ignore[method-assign]
    elif route_name == "library_list":
        container.library.list = lambda _collection: malformed  # type: ignore[method-assign]
    else:
        container.library.save = lambda *_args, **_kwargs: malformed[0]  # type: ignore[method-assign]

    response = TestClient(create_app(container=container), raise_server_exceptions=False).request(
        method, path, headers=_auth(), json=payload
    )

    assert response.status_code == 500
    assert "documented contract" not in response.text


def test_public_settings_patch_cannot_complete_onboarding(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/settings",
        headers=_auth(),
        json={"onboarding_complete": True},
    )

    assert response.status_code == 422
    assert client.get("/api/v1/settings", headers=_auth()).json()["onboarding_complete"] is False


def test_public_settings_partial_source_maps_merge_and_validate(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/settings",
        headers=_auth(),
        json={
            "source_enabled": {"bilibili": True},
            "source_weights": {"youtube": 2.5},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["source_enabled"]) == 7
    assert len(payload["source_weights"]) == 7
    assert payload["source_enabled"]["bilibili"] is True
    assert payload["source_enabled"]["youtube"] is False
    assert payload["source_weights"]["youtube"] == 2.5
    assert payload["source_weights"]["bilibili"] == 1.0

    for invalid in (
        {"source_enabled": {"unknown": True}},
        {"source_enabled": {"bilibili": "yes"}},
        {"source_weights": {"bilibili": -0.1}},
    ):
        assert client.patch("/api/v1/settings", headers=_auth(), json=invalid).status_code == 422


def test_public_job_priority_is_a_bounded_lane_not_an_arbitrary_integer() -> None:
    container = _container()
    client = TestClient(create_app(container=container))

    accepted = client.post(
        "/api/v1/jobs",
        headers=_auth(),
        json={
            "job_name": "source_sync",
            "idempotency_key": "manual",
            "priority": "user-triggered",
        },
    )
    rejected = client.post(
        "/api/v1/jobs",
        headers=_auth(),
        json={"job_name": "source_sync", "idempotency_key": "starve", "priority": 999999},
    )

    assert accepted.status_code == 202
    assert container.jobs.last_priority == 50  # type: ignore[attr-defined]
    assert rejected.status_code == 422


def test_source_task_long_poll_complete_and_secret_payload_rejection(client: TestClient) -> None:
    headers = _auth()
    response = client.get(
        "/api/v1/source-tasks/claim?source_id=bilibili&wait_seconds=0", headers=headers
    )
    assert response.status_code == 204
    task_id = uuid4()
    assert (
        client.post(
            f"/api/v1/source-tasks/{task_id}/complete",
            headers=headers,
            json={"lease_token": "x" * 20, "result": {"items": []}},
        ).status_code
        == 200
    )
    rejected = client.post(
        f"/api/v1/source-tasks/{task_id}/complete",
        headers=headers,
        json={"lease_token": "x" * 20, "result": {"api_key": "must-not-echo"}},
    )
    assert rejected.status_code == 422
    assert "must-not-echo" not in rejected.text


def test_chat_and_job_progress_are_standard_sse(client: TestClient) -> None:
    headers = _auth()
    chat = client.post(
        "/api/v1/chat/stream",
        headers=headers,
        json={"conversation_id": str(uuid4()), "message": "hello"},
    )
    assert chat.headers["content-type"].startswith("text/event-stream")
    assert "event: delta\n" in chat.text
    assert "event: done\n" in chat.text
    assert chat.text.endswith("\n\n")

    run = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={"job_name": "source_sync", "idempotency_key": "manual"},
    ).json()
    progress = client.get(f"/api/v1/jobs/{run['id']}/events", headers=headers)
    assert progress.headers["content-type"].startswith("text/event-stream")
    assert "event: progress\n" in progress.text
    assert "event: done\n" in progress.text


def test_onboarding_public_only_until_completed() -> None:
    container = _container()
    client = TestClient(create_app(container=container))
    assert client.get("/api/v1/onboarding").status_code == 200
    response = client.post("/api/v1/onboarding/start", json={"source_ids": ["bilibili"]})
    assert response.status_code == 202
    container.settings.value = container.settings.value.model_copy(
        update={"onboarding_complete": True}
    )
    assert client.get("/api/v1/onboarding").status_code == 401


@pytest.mark.parametrize("payload", [{}, {"source_ids": []}])
def test_onboarding_rejects_an_empty_source_selection(payload: dict[str, object]) -> None:
    container = _container()
    client = TestClient(create_app(container=container))

    response = client.post("/api/v1/onboarding/start", json=payload)

    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "validation_error", "message": "request validation failed"}
    }


def test_domain_errors_map_without_leaking_values(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/settings", headers=_auth(), json={"feed_low_watermark": 99_999}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_missing_and_unavailable_errors_are_stable_and_safe() -> None:
    container = _container()
    container.profile.snapshot = None
    client = TestClient(create_app(container=container))
    assert client.get("/api/v1/profile", headers=_auth()).status_code == 404

    def unavailable() -> UserSettings:
        raise DependencyUnavailableError("detail-that-must-not-leak")

    container.settings.get = unavailable
    response = client.get("/api/v1/settings", headers=_auth())
    assert response.status_code == 503
    assert "detail-that-must-not-leak" not in response.text


async def test_chat_disconnect_stops_before_calling_service() -> None:
    called = False

    class Chat:
        async def stream(self, **_kwargs):
            nonlocal called
            called = True
            yield None

    class DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    container = _container()
    container.chat = Chat()
    iterator = _chat_events(
        ChatRequest(conversation_id=uuid4(), message="hello"),
        DisconnectedRequest(),  # type: ignore[arg-type]
        container,
    )
    assert [event async for event in iterator] == []
    assert not called


@pytest.mark.asyncio
async def test_onboarding_stream_lookup_error_closes_with_typed_error_only() -> None:
    container = _container()

    def unavailable(_run_id: UUID):
        raise DependencyUnavailableError("private upstream detail")

    container.onboarding.progress = unavailable  # type: ignore[method-assign]
    events = [
        event
        async for event in _progress_events(
            container.jobs.run.id,
            _ConnectedRequest(),  # type: ignore[arg-type]
            container,
        )
    ]

    assert events == ['event: error\ndata: {"code":"onboarding_status_unavailable"}\n\n']
    assert "private upstream detail" not in "".join(events)


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


def _release_after_event_loop_progress(
    started: Event,
    progressed: Event,
    release: Event,
    observed: list[bool],
) -> Thread:
    def watch() -> None:
        assert started.wait(1)
        observed.append(progressed.wait(0.5))
        release.set()

    thread = Thread(target=watch, daemon=True)
    thread.start()
    return thread


@pytest.mark.asyncio
async def test_source_claim_sync_port_does_not_block_the_event_loop() -> None:
    started = Event()
    progressed = Event()
    release = Event()
    observed: list[bool] = []

    class BlockingSourceTasks(_SourceTasks):
        def claim(self, source_id: str):
            del source_id
            started.set()
            assert release.wait(2)
            return None

    container = _container()
    container.source_tasks = BlockingSourceTasks()
    watcher = _release_after_event_loop_progress(started, progressed, release, observed)

    async def claim() -> None:
        await claim_source_task(
            _ConnectedRequest(),  # type: ignore[arg-type]
            SourceId.BILIBILI,
            container,
            wait_seconds=0,
        )

    async def mark_progress() -> None:
        while not started.is_set():
            await anyio.sleep(0)
        progressed.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(claim)
        tasks.start_soon(mark_progress)

    watcher.join(timeout=1)
    assert observed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("event_stream", [_job_events, _progress_events])
async def test_job_progress_sync_port_does_not_block_the_event_loop(event_stream) -> None:  # type: ignore[no-untyped-def]
    started = Event()
    progressed = Event()
    release = Event()
    observed: list[bool] = []
    container = _container()
    original = container.jobs.inspect

    def blocking_inspect(run_id: UUID):  # type: ignore[no-untyped-def]
        started.set()
        assert release.wait(2)
        return original(run_id)

    container.jobs.inspect = blocking_inspect  # type: ignore[method-assign]
    watcher = _release_after_event_loop_progress(started, progressed, release, observed)

    async def consume() -> None:
        events = [
            event
            async for event in event_stream(
                container.jobs.run.id,
                _ConnectedRequest(),  # type: ignore[arg-type]
                container,
            )
        ]
        assert events

    async def mark_progress() -> None:
        while not started.is_set():
            await anyio.sleep(0)
        progressed.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(consume)
        tasks.start_soon(mark_progress)

    watcher.join(timeout=1)
    assert observed == [True]


def test_startup_failure_still_runs_shutdown_cleanup() -> None:
    lifecycle: list[str] = []

    def startup() -> None:
        lifecycle.append("startup")
        raise RuntimeError("startup failed")

    def shutdown() -> None:
        lifecycle.append("shutdown")

    container = _container()
    container.startup_hook = startup
    container.shutdown_hook = shutdown

    with (
        pytest.raises(RuntimeError, match="startup failed"),
        TestClient(create_app(container=container)),
    ):
        pass

    assert lifecycle == ["startup", "shutdown"]


def test_missing_runtime_access_token_fails_closed() -> None:
    client = TestClient(create_app(container=_container(token="")))
    response = client.get("/api/v1/settings")
    assert response.status_code == 503
    assert "token" not in response.text.casefold()


def test_default_composition_serves_migrated_db_without_live_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "vnext.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("OPENBILICLAW_ACCESS_TOKEN", "local-test-token")
    monkeypatch.delenv("OPENBILICLAW_LITELLM_API_KEY", raising=False)
    with TestClient(create_app()) as client:
        headers = {"Authorization": "Bearer local-test-token"}
        assert client.get("/api/v1/settings", headers=headers).status_code == 200
        health = client.get("/api/v1/system/ai-health", headers=headers).json()
        assert health["proxy_reachable"] is False
        assert len(health["aliases"]) == 3


def test_legacy_and_websocket_routes_are_absent(client: TestClient) -> None:
    legacy_paths = (
        "/api/health",
        "/api/recommendations",
        "/api/bilibili/tasks/claim",
        "/api/update/check",
        "/api/delight",
        "/api/probes",
        "/api/saved/sync",
        "/api/debug/e2e",
    )
    assert all(client.get(path).status_code == 404 for path in legacy_paths)
    assert all(route.__class__.__name__ != "APIWebSocketRoute" for route in client.app.routes)


def test_error_response_does_not_echo_authorization(client: TestClient) -> None:
    secret = "not-a-real-secret-but-must-not-leak"
    response = client.get("/api/v1/settings", headers={"Authorization": f"Bearer {secret}"})
    assert secret not in response.text
    assert response.json()["error"]["message"] == "access denied"
