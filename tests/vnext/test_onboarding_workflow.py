"""Durable retained-journey tests for the vNext onboarding workflow."""

from __future__ import annotations

import json
import time
from threading import Thread
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.dependencies import (
    AccessPolicy,
    ApplicationContainer,
    require_onboarding_access,
)
from openbiliclaw.api.routers.onboarding import _progress_events
from openbiliclaw.features.system.service import OnboardingService, SettingsService
from openbiliclaw.infrastructure.database.base import (
    DatabaseSettings,
    create_engine_and_session,
)
from openbiliclaw.infrastructure.database.uow import UnitOfWork
from openbiliclaw.infrastructure.jobs.queue import build_huey
from openbiliclaw.infrastructure.jobs.tasks import (
    HueyJobQueue,
    JobRunStatus,
    JobService,
)

if TYPE_CHECKING:
    from pathlib import Path


def _database(tmp_path: Path) -> tuple[Any, Any]:
    url = f"sqlite:///{tmp_path / 'onboarding.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return create_engine_and_session(DatabaseSettings(url=url))


class RecordingQueue:
    def __init__(self) -> None:
        self.messages: list[tuple[str, UUID, int]] = []

    def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None:
        self.messages.append((job_name, run_id, priority))


class FailAfterRootQueue(RecordingQueue):
    def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None:
        if job_name != "source_sync":
            raise ConnectionError("queue temporarily unavailable")
        super().enqueue(job_name, run_id, priority)


class ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


def test_public_settings_cannot_complete_or_reopen_onboarding(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    settings = SettingsService(lambda: UnitOfWork(session_factory))

    with pytest.raises(ValueError, match="workflow-owned"):
        settings.update({"onboarding_complete": True})
    settings.complete_onboarding()
    with pytest.raises(ValueError, match="workflow-owned"):
        settings.update({"onboarding_complete": False})

    assert settings.get().onboarding_complete is True
    engine.dispose()


def test_onboarding_access_window_closes_only_after_workflow_completion(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    container = SimpleNamespace(settings=settings, access=AccessPolicy(token="test-access-token"))
    anonymous = Request({"type": "http", "headers": []})

    require_onboarding_access(anonymous, container)  # type: ignore[arg-type]
    settings.complete_onboarding()

    with pytest.raises(HTTPException) as denied:
        require_onboarding_access(anonymous, container)  # type: ignore[arg-type]
    assert denied.value.status_code == 401

    authorized = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer test-access-token")],
        }
    )
    require_onboarding_access(authorized, container)  # type: ignore[arg-type]
    engine.dispose()


@pytest.mark.parametrize("terminal", [JobRunStatus.FAILED, JobRunStatus.CANCELLED])
def test_onboarding_chain_advances_only_after_terminal_success(
    tmp_path: Path, terminal: JobRunStatus
) -> None:
    engine, session_factory = _database(tmp_path)
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    queue = RecordingQueue()
    jobs = JobService(lambda: UnitOfWork(session_factory), queue=queue)
    onboarding = OnboardingService(settings, jobs)

    source = onboarding.start(("bilibili",))
    assert jobs.claim(source.id)
    if terminal is JobRunStatus.FAILED:
        jobs.fail(source.id, RuntimeError("transport failed"))
    else:
        jobs.cancel(source.id)

    assert [message[0] for message in queue.messages] == ["source_sync"]
    assert settings.get().onboarding_complete is False
    engine.dispose()


def test_onboarding_service_rejects_empty_sources_before_scheduling(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    queue = RecordingQueue()
    onboarding = OnboardingService(
        settings,
        JobService(lambda: UnitOfWork(session_factory), queue=queue),
    )

    with pytest.raises(ValueError, match="at least one source"):
        onboarding.start(())

    assert queue.messages == []
    assert all(enabled is False for enabled in settings.get().source_enabled.values())
    engine.dispose()


@pytest.mark.parametrize("terminal", [JobRunStatus.FAILED, JobRunStatus.CANCELLED])
def test_explicit_onboarding_restart_resumes_failed_or_cancelled_stage(
    tmp_path: Path, terminal: JobRunStatus
) -> None:
    engine, session_factory = _database(tmp_path)
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    queue = RecordingQueue()
    jobs = JobService(lambda: UnitOfWork(session_factory), queue=queue)
    onboarding = OnboardingService(settings, jobs)
    source = onboarding.start(("bilibili",))
    assert jobs.claim(source.id)
    jobs.succeed(source.id)
    profile = next(run for run in jobs.list() if run.job_name == "profile_projection")
    assert jobs.claim(profile.id)
    if terminal is JobRunStatus.FAILED:
        jobs.fail(profile.id, RuntimeError("profile unavailable"))
    else:
        jobs.cancel(profile.id)

    resumed_root = onboarding.start(("bilibili",))
    resumed_profile = jobs.inspect(profile.id)

    assert resumed_root.id == source.id
    assert resumed_profile.status is JobRunStatus.PENDING
    assert resumed_profile.error is None
    assert [name for name, _, _ in queue.messages] == [
        "source_sync",
        "profile_projection",
        "profile_projection",
    ]
    assert settings.get().onboarding_complete is False
    engine.dispose()


def test_restart_reconciles_success_gap_idempotently(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    first_queue = RecordingQueue()
    first_jobs = JobService(lambda: UnitOfWork(session_factory), queue=first_queue)
    source = first_jobs.schedule("source_sync", idempotency_key="onboarding:bilibili")
    assert first_jobs.claim(source.id)
    first_jobs.succeed(source.id)

    restarted_queue = RecordingQueue()
    restarted_jobs = JobService(lambda: UnitOfWork(session_factory), queue=restarted_queue)
    OnboardingService(settings, restarted_jobs)
    restarted_jobs.recover_interrupted()
    restarted_jobs.recover_interrupted()

    runs = restarted_jobs.list()
    profile = next(run for run in runs if run.job_name == "profile_projection")
    assert profile.idempotency_key == "profile_projection:onboarding:bilibili"
    assert {message[0] for message in restarted_queue.messages} == {"profile_projection"}
    assert {message[1] for message in restarted_queue.messages} == {profile.id}
    assert len([run for run in runs if run.job_name == "profile_projection"]) == 1
    assert settings.get().onboarding_complete is False
    engine.dispose()


def test_workflow_progress_resolves_persisted_child_after_process_restart(
    tmp_path: Path,
) -> None:
    engine, session_factory = _database(tmp_path)
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    first_jobs = JobService(lambda: UnitOfWork(session_factory), queue=RecordingQueue())
    first_onboarding = OnboardingService(settings, first_jobs)
    root = first_onboarding.start(("bilibili",))
    assert first_jobs.claim(root.id)
    first_jobs.succeed(root.id)
    profile = next(run for run in first_jobs.list() if run.job_name == "profile_projection")

    restarted_jobs = JobService(lambda: UnitOfWork(session_factory), queue=RecordingQueue())
    restarted = OnboardingService(settings, restarted_jobs)
    progress = restarted.progress(root.id)

    assert progress.root_run_id == root.id
    assert progress.stage == "profile_projection"
    assert progress.run.id == profile.id
    assert progress.onboarding_complete is False
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [JobRunStatus.FAILED, JobRunStatus.CANCELLED])
async def test_stream_propagates_terminal_child_status_and_identity(
    tmp_path: Path, terminal: JobRunStatus
) -> None:
    engine, session_factory = _database(tmp_path)
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    jobs = JobService(lambda: UnitOfWork(session_factory), queue=RecordingQueue())
    onboarding = OnboardingService(settings, jobs)
    root = onboarding.start(("bilibili",))
    assert jobs.claim(root.id)
    jobs.succeed(root.id)
    profile = next(run for run in jobs.list() if run.job_name == "profile_projection")
    assert jobs.claim(profile.id)
    if terminal is JobRunStatus.FAILED:
        jobs.fail(profile.id, RuntimeError("private detail"))
    else:
        jobs.cancel(profile.id)

    events = [
        event
        async for event in _progress_events(
            root.id,
            ConnectedRequest(),  # type: ignore[arg-type]
            SimpleNamespace(onboarding=onboarding),  # type: ignore[arg-type]
        )
    ]
    terminal_payload = json.loads(events[-1].split("data: ", 1)[1])

    assert events[-1].startswith("event: done\n")
    assert terminal_payload == {
        "root_run_id": str(root.id),
        "stage": "profile_projection",
        "run_id": str(profile.id),
        "status": terminal.value,
        "onboarding_complete": False,
    }
    assert "private detail" not in "".join(events)
    engine.dispose()


def test_restart_recovers_child_persisted_before_queue_dispatch_failure(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    jobs = JobService(lambda: UnitOfWork(session_factory), queue=FailAfterRootQueue())
    onboarding = OnboardingService(settings, jobs)
    source = onboarding.start(("bilibili",))
    assert jobs.claim(source.id)

    jobs.succeed(source.id)

    profile = next(run for run in jobs.list() if run.job_name == "profile_projection")
    assert jobs.inspect(source.id).status is JobRunStatus.SUCCEEDED
    assert profile.status is JobRunStatus.PENDING
    assert profile.dispatched_at is None

    recovered_queue = RecordingQueue()
    restarted = JobService(lambda: UnitOfWork(session_factory), queue=recovered_queue)
    OnboardingService(settings, restarted)
    restarted.recover_interrupted()

    assert [(name, run_id) for name, run_id, _ in recovered_queue.messages] == [
        ("profile_projection", profile.id)
    ]
    assert restarted.inspect(profile.id).dispatched_at is not None
    engine.dispose()


def test_app_database_and_file_queue_complete_full_onboarding_once(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    transport = build_huey(tmp_path / "huey.db")
    runtime: dict[str, JobService] = {}

    def execute(job_name: str, run_id: str) -> None:
        service = runtime["service"]
        resolved = UUID(run_id)
        assert service.claim(resolved)
        service.succeed(resolved)

    tasks: dict[str, Any] = {}
    for job_name in ("source_sync", "profile_projection", "feed_replenishment"):

        @transport.task(name=f"onboarding-{job_name}")
        def execute_stage(run_id: str, *, _job_name: str = job_name) -> None:
            execute(_job_name, run_id)

        tasks[job_name] = execute_stage

    queue = HueyJobQueue(tasks=tasks)
    settings = SettingsService(lambda: UnitOfWork(session_factory))
    service = JobService(lambda: UnitOfWork(session_factory), queue=queue)
    runtime["service"] = service
    onboarding = OnboardingService(settings, service)

    root = onboarding.start(("bilibili", "youtube"))
    assert root.job_name == "source_sync"

    executed: list[str] = []
    while message := transport.dequeue():
        executed.append(message.name.removeprefix("onboarding-"))
        transport.execute(message)

    assert executed == ["source_sync", "profile_projection", "feed_replenishment"]
    assert settings.get().onboarding_complete is True
    assert [run.status for run in service.list()] == [
        JobRunStatus.SUCCEEDED,
        JobRunStatus.SUCCEEDED,
        JobRunStatus.SUCCEEDED,
    ]

    # Re-instantiating both services models process restart. Successful stages are replay-safe.
    restarted = JobService(lambda: UnitOfWork(session_factory), queue=queue)
    OnboardingService(SettingsService(lambda: UnitOfWork(session_factory)), restarted)
    restarted.recover_interrupted()
    assert transport.dequeue() is None
    assert len(restarted.list()) == 3
    engine.dispose()


def test_real_app_stream_follows_durable_onboarding_children(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    transport = build_huey(tmp_path / "stream-huey.db")
    runtime: dict[str, JobService] = {}
    tasks: dict[str, Any] = {}
    for job_name in ("source_sync", "profile_projection", "feed_replenishment"):

        @transport.task(name=f"stream-{job_name}")
        def execute_stage(run_id: str, *, _job_name: str = job_name) -> None:
            del _job_name
            service = runtime["service"]
            resolved = UUID(run_id)
            if service.claim(resolved):
                time.sleep(0.3)
                service.succeed(resolved)

        tasks[job_name] = execute_stage

    settings = SettingsService(lambda: UnitOfWork(session_factory))
    jobs = JobService(
        lambda: UnitOfWork(session_factory),
        queue=HueyJobQueue(tasks=tasks),
    )
    runtime["service"] = jobs
    onboarding = OnboardingService(settings, jobs)
    unavailable = SimpleNamespace()
    container = ApplicationContainer(
        access=AccessPolicy(token="test-access-token"),
        settings=settings,
        onboarding=onboarding,
        sources=unavailable,
        source_tasks=unavailable,
        activity=unavailable,
        profile=unavailable,
        feed=unavailable,
        feedback=unavailable,
        library=unavailable,
        chat=unavailable,
        jobs=jobs,
        ai_health=unavailable,
    )

    with TestClient(create_app(container=container)) as client:
        started = client.post(
            "/api/v1/onboarding/start",
            json={"source_ids": ["bilibili"]},
        )
        assert started.status_code == 202
        root_id = UUID(started.json()["id"])

        def execute_queue() -> None:
            while not settings.get().onboarding_complete:
                message = transport.dequeue()
                if message is None:
                    time.sleep(0.01)
                    continue
                transport.execute(message)

        worker = Thread(target=execute_queue, daemon=True)
        worker.start()
        streamed = client.get(f"/api/v1/onboarding/{root_id}/events")
        worker.join(timeout=3)

    assert streamed.status_code == 200
    events: list[tuple[str, dict[str, object]]] = []
    for frame in streamed.text.strip().split("\n\n"):
        event_line, data_line = frame.splitlines()
        events.append(
            (
                event_line.removeprefix("event: "),
                json.loads(data_line.removeprefix("data: ")),
            )
        )
    progress = [payload for event, payload in events if event == "progress"]
    observed_stages = list(dict.fromkeys(str(payload["stage"]) for payload in progress))
    child_ids = {
        str(payload["stage"]): str(payload["run"]["id"])  # type: ignore[index]
        for payload in progress
    }
    done = events[-1]

    assert observed_stages == ["source_sync", "profile_projection", "feed_replenishment"]
    assert child_ids["source_sync"] == str(root_id)
    assert child_ids["profile_projection"] != str(root_id)
    assert child_ids["feed_replenishment"] not in {
        str(root_id),
        child_ids["profile_projection"],
    }
    assert done == (
        "done",
        {
            "root_run_id": str(root_id),
            "stage": "feed_replenishment",
            "run_id": child_ids["feed_replenishment"],
            "status": "succeeded",
            "onboarding_complete": True,
        },
    )
    assert settings.get().onboarding_complete is True
    engine.dispose()
