from __future__ import annotations

from collections.abc import AsyncIterator  # noqa: TC003 - async fake protocol clarity
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 - pytest resolves fixture annotations
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.dependencies import (
    AccessPolicy,
    ApplicationContainer,
    DependencyUnavailableError,
)
from openbiliclaw.api.routers.chat import ChatRequest, _chat_events
from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind
from openbiliclaw.features.chat.service import ChatChunk, ChatChunkKind
from openbiliclaw.features.library.domain import CollectionKind  # noqa: TC001
from openbiliclaw.features.profile.domain import ProfileSnapshot
from openbiliclaw.features.sources.domain import SourceId, SourceTaskCompletion
from openbiliclaw.features.system.domain import UserSettings
from openbiliclaw.infrastructure.ai.health import AIHealthResult, AliasHealth
from openbiliclaw.infrastructure.jobs.tasks import JobRunSnapshot, JobRunStatus


class _Settings:
    value = UserSettings()

    def get(self) -> UserSettings:
        return self.value

    def update(self, patch: dict[str, object]) -> UserSettings:
        self.value = UserSettings.model_validate({**self.value.model_dump(), **patch})
        return self.value


class _Activity:
    def ingest(self, event: ActivityEvent) -> tuple[object, ...]:
        return (SimpleNamespace(model_dump=lambda mode=None: {"event_id": str(event.id)}),)


class _Profile:
    snapshot = ProfileSnapshot(revision=2, narrative="evidence profile")

    def current(self) -> ProfileSnapshot | None:
        return self.snapshot


class _Feed:
    def list_entries(self, *, limit: int = 50, offset: int = 0) -> tuple[object, ...]:
        return ()


class _Feedback:
    def record(self, interaction: object) -> object:
        return SimpleNamespace(model_dump=lambda mode=None: {"facet": "interests"})


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
        self.run = JobRunSnapshot(
            id=uuid4(),
            job_name="source_sync",
            idempotency_key="source_sync:test",
            status=JobRunStatus.PENDING,
            priority=100,
            progress=0,
        )

    def schedule(self, job_name: str, *, idempotency_key: str, priority: int | None = None):
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
            SimpleNamespace(
                model_dump=lambda mode=None: {
                    "source_id": "bilibili",
                    "display_name": "Bilibili",
                    "capabilities": ["bootstrap_import"],
                    "operations": [],
                }
            ),
        )

    def statuses(self):
        return ({"source_id": "bilibili", "configured": False, "enabled": False},)

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


def test_missing_runtime_access_token_fails_closed() -> None:
    client = TestClient(create_app(container=_container(token="")))
    response = client.get("/api/v1/settings")
    assert response.status_code == 503
    assert "token" not in response.text.casefold()


def test_default_composition_migrates_fresh_db_without_live_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "vnext.db"
    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("OPENBILICLAW_ACCESS_TOKEN", "local-test-token")
    monkeypatch.delenv("OPENBILICLAW_LITELLM_API_KEY", raising=False)
    with TestClient(create_app()) as client:
        headers = {"Authorization": "Bearer local-test-token"}
        assert client.get("/api/v1/settings", headers=headers).status_code == 200
        health = client.get("/api/v1/system/ai-health", headers=headers).json()
        assert health["proxy_reachable"] is False
        assert len(health["aliases"]) == 3
    assert database.is_file()


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
