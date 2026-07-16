"""Business-state and Huey transport tests for the four vNext jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from huey.api import TaskLock, TaskWrapper

from openbiliclaw.infrastructure.jobs.queue import (
    PRIORITY_INTERACTIVE,
    PRIORITY_SCHEDULED,
    PRIORITY_USER_TRIGGERED,
    build_huey,
)
from openbiliclaw.infrastructure.jobs.tasks import (
    JOB_NAMES,
    HueyJobQueue,
    JobCancelledError,
    JobExecutionContext,
    JobRunStatus,
    JobService,
    PermanentJobError,
    TransientJobError,
    classify_retry,
    cleanup,
    feed_replenishment,
    profile_projection,
    source_sync,
)


class MemoryJobs:
    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, object]] = {}

    def create_or_get(
        self, *, job_name: str, idempotency_key: str, priority: int
    ) -> tuple[UUID, bool]:
        for run_id, row in self.rows.items():
            if row["idempotency_key"] == idempotency_key:
                return run_id, False
        run_id = UUID(int=len(self.rows) + 1)
        self.rows[run_id] = {
            "job_name": job_name,
            "idempotency_key": idempotency_key,
            "priority": priority,
            "status": JobRunStatus.PENDING,
            "progress": 0.0,
            "error": None,
            "attempts": 0,
            "dispatched_at": None,
        }
        return run_id, True

    def get(self, run_id: UUID) -> dict[str, object]:
        return self.rows[run_id]

    def claim(self, run_id: UUID) -> bool:
        row = self.rows[run_id]
        if row["status"] is not JobRunStatus.PENDING:
            return False
        row["status"] = JobRunStatus.RUNNING
        row["attempts"] = int(row["attempts"]) + 1
        return True

    def mark_dispatched(self, run_id: UUID) -> None:
        self.rows[run_id]["dispatched_at"] = datetime.now(UTC)

    def pending_undispatched(self) -> tuple[UUID, ...]:
        return tuple(
            run_id
            for run_id, row in self.rows.items()
            if row["status"] is JobRunStatus.PENDING and row["dispatched_at"] is None
        )

    def pending(self) -> tuple[UUID, ...]:
        return tuple(
            run_id for run_id, row in self.rows.items() if row["status"] is JobRunStatus.PENDING
        )

    def guard_running(self, run_id: UUID) -> bool:
        return self.rows[run_id]["status"] is JobRunStatus.RUNNING

    def checkpoint(self, run_id: UUID, progress: float) -> bool:
        row = self.rows[run_id]
        if row["status"] is not JobRunStatus.RUNNING:
            return False
        row["progress"] = max(float(row["progress"]), progress)
        return True

    def update(
        self,
        run_id: UUID,
        *,
        status: JobRunStatus,
        progress: float,
        error: str | None = None,
    ) -> None:
        self.rows[run_id].update(status=status, progress=progress, error=error)
        if status is JobRunStatus.PENDING:
            self.rows[run_id]["dispatched_at"] = None

    def cancel(self, run_id: UUID) -> bool:
        row = self.rows[run_id]
        if row["status"] in {JobRunStatus.SUCCEEDED, JobRunStatus.FAILED}:
            return False
        row["status"] = JobRunStatus.CANCELLED
        return True

    def recover_running(self) -> tuple[UUID, ...]:
        recovered = []
        for run_id, row in self.rows.items():
            if row["status"] is JobRunStatus.RUNNING:
                row["status"] = JobRunStatus.PENDING
                row["dispatched_at"] = None
                recovered.append(run_id)
        return tuple(recovered)

    def cleanup_finished(self, *, older_than: object) -> int:
        del older_than
        return 0


class JobUow:
    def __init__(self, repository: MemoryJobs) -> None:
        self.job_runs = repository

    def __enter__(self) -> JobUow:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def commit(self) -> None:
        return None


class Queue:
    def __init__(self, *, fail: bool = False) -> None:
        self.enqueued: list[tuple[str, UUID, int]] = []
        self.fail = fail

    def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None:
        if self.fail:
            raise ConnectionError("queue unavailable")
        self.enqueued.append((job_name, run_id, priority))


def test_queue_uses_separate_sqlite_file_and_three_ordered_priorities(tmp_path: Path) -> None:
    path = tmp_path / "data" / "vnext" / "huey.db"

    huey = build_huey(path)

    assert path.parent.is_dir()
    assert huey.storage.filename == str(path)
    assert PRIORITY_INTERACTIVE > PRIORITY_USER_TRIGGERED > PRIORITY_SCHEDULED


def test_exactly_four_job_names_are_public() -> None:
    assert JOB_NAMES == (
        "source_sync",
        "profile_projection",
        "feed_replenishment",
        "cleanup",
    )


@pytest.mark.parametrize(
    ("wrapper", "lock_name", "priority", "retries"),
    [
        (source_sync, "source-sync", PRIORITY_SCHEDULED, 2),
        (profile_projection, "profile-projection", PRIORITY_SCHEDULED, 2),
        (feed_replenishment, "feed-replenishment", PRIORITY_USER_TRIGGERED, 2),
        (cleanup, "cleanup", PRIORITY_SCHEDULED, 1),
    ],
)
def test_all_four_periodic_wrappers_have_priority_retry_and_lock(
    wrapper: TaskWrapper,
    lock_name: str,
    priority: int,
    retries: int,
) -> None:
    locks = [
        cell.cell_contents
        for cell in wrapper.func.__closure__ or ()
        if isinstance(cell.cell_contents, TaskLock)
    ]

    assert [lock._name for lock in locks] == [lock_name]  # noqa: SLF001
    assert wrapper.settings["default_priority"] == priority
    assert wrapper.settings["default_retries"] == retries


def test_schedule_is_idempotent_and_suppresses_duplicate_transport_messages() -> None:
    jobs = MemoryJobs()
    queue = Queue()
    service = JobService(lambda: JobUow(jobs), queue=queue)

    first = service.schedule("source_sync", idempotency_key="daily:2026-07-17")
    second = service.schedule("source_sync", idempotency_key="daily:2026-07-17")

    assert first.id == second.id
    assert len(queue.enqueued) == 1
    assert service.inspect(first.id).status is JobRunStatus.PENDING


def test_queue_failure_leaves_recoverable_dispatch_and_duplicate_schedule_retries() -> None:
    jobs = MemoryJobs()
    queue = Queue(fail=True)
    service = JobService(lambda: JobUow(jobs), queue=queue)

    with pytest.raises(ConnectionError, match="unavailable"):
        service.schedule("source_sync", idempotency_key="recoverable")
    run_id = next(iter(jobs.rows))
    assert jobs.rows[run_id]["dispatched_at"] is None

    queue.fail = False
    duplicate = service.schedule("source_sync", idempotency_key="recoverable")

    assert duplicate.id == run_id
    assert len(queue.enqueued) == 1
    assert jobs.rows[run_id]["dispatched_at"] is not None


def test_startup_reconciles_pending_undispatched_rows() -> None:
    jobs = MemoryJobs()
    queue = Queue(fail=True)
    service = JobService(lambda: JobUow(jobs), queue=queue)
    with pytest.raises(ConnectionError):
        service.schedule("cleanup", idempotency_key="restart")

    queue.fail = False
    assert service.reconcile_pending_dispatches() == (next(iter(jobs.rows)),)
    assert len(queue.enqueued) == 1


def test_huey_job_queue_uses_immediate_wrapper_and_real_sqlite_execution(tmp_path: Path) -> None:
    transport = build_huey(tmp_path / "real-handoff.db")
    executed: list[str] = []

    @transport.task(name="source_sync")
    def local_source_sync(run_id: str) -> None:
        executed.append(run_id)

    queue = HueyJobQueue(tasks={"source_sync": local_source_sync})
    run_id = UUID(int=99)

    queue.enqueue("source_sync", run_id, PRIORITY_USER_TRIGGERED)
    message = transport.dequeue()
    assert message is not None
    transport.execute(message)

    assert executed == [str(run_id)]


def test_huey_transport_result_cannot_override_business_status(tmp_path: Path) -> None:
    jobs = MemoryJobs()
    service = JobService(lambda: JobUow(jobs), queue=Queue())
    run = service.schedule("source_sync", idempotency_key="authority")
    transport = build_huey(tmp_path / "transport.db")

    transport.put_result(str(run.id), {"status": "succeeded"})

    assert transport.result(str(run.id), preserve=True) == {"status": "succeeded"}
    assert service.inspect(run.id).status is JobRunStatus.PENDING


def test_cancellation_is_business_state_and_prevents_claim() -> None:
    jobs = MemoryJobs()
    service = JobService(lambda: JobUow(jobs), queue=Queue())
    run = service.schedule("cleanup", idempotency_key="cleanup:once")

    assert service.cancel(run.id).status is JobRunStatus.CANCELLED
    assert service.claim(run.id) is False


def test_restart_recovery_requeues_only_application_running_rows() -> None:
    jobs = MemoryJobs()
    queue = Queue()
    service = JobService(lambda: JobUow(jobs), queue=queue)
    run = service.schedule("feed_replenishment", idempotency_key="feed:1")
    assert service.claim(run.id) is True

    recovered = service.recover_interrupted()

    assert recovered == (run.id,)
    assert len(queue.enqueued) == 2
    assert service.inspect(run.id).status is JobRunStatus.PENDING


def test_running_cancellation_is_visible_at_checkpoint_and_progress_is_monotonic() -> None:
    jobs = MemoryJobs()
    service = JobService(lambda: JobUow(jobs), queue=Queue())
    run = service.schedule("feed_replenishment", idempotency_key="progress")
    assert service.claim(run.id)
    context = JobExecutionContext(service, run.id)

    context.checkpoint(0.4)
    context.checkpoint(0.2)
    assert service.inspect(run.id).progress == 0.4

    service.cancel(run.id)
    with pytest.raises(JobCancelledError):
        context.checkpoint(0.8)
    assert service.inspect(run.id).status is JobRunStatus.CANCELLED
    assert service.inspect(run.id).progress == 0.4


def test_retry_keeps_monotonic_business_progress() -> None:
    jobs = MemoryJobs()
    service = JobService(lambda: JobUow(jobs), queue=Queue())
    run = service.schedule("source_sync", idempotency_key="retry-progress")
    assert service.claim(run.id)
    first = JobExecutionContext(service, run.id)
    first.checkpoint(0.6)
    service.retry(run.id, TransientJobError("retry"))
    assert service.claim(run.id)

    JobExecutionContext(service, run.id).checkpoint(0.3)

    assert service.inspect(run.id).progress == 0.6


@pytest.mark.parametrize("interval", [5, 30])
def test_source_sync_periodic_buckets_follow_typed_setting(interval: int) -> None:
    jobs = MemoryJobs()
    queue = Queue()
    service = JobService(
        lambda: JobUow(jobs),
        queue=queue,
        source_sync_interval_minutes=lambda: interval,
    )
    now = datetime(2026, 7, 17, 1, 0, tzinfo=UTC)

    first = service.schedule_periodic("source_sync", now=now)
    duplicate = service.schedule_periodic("source_sync", now=now + timedelta(minutes=interval - 1))
    later = service.schedule_periodic("source_sync", now=now + timedelta(minutes=interval))

    assert first.id == duplicate.id
    assert later.id != first.id
    assert len(queue.enqueued) == 2


@pytest.mark.parametrize("compose_name", ["docker-compose.yml", "docker-compose.prebuilt.yml"])
def test_backend_and_worker_share_vnext_database_but_not_huey(compose_name: str) -> None:
    compose = yaml.safe_load((Path(__file__).parents[2] / compose_name).read_text())
    services: dict[str, Any] = compose["services"]
    backend_db = services["openbiliclaw-backend"]["environment"]["OPENBILICLAW_DATABASE_URL"]
    worker = services["worker"]["environment"]

    assert backend_db == worker["OPENBILICLAW_DATABASE_URL"]
    assert worker["OPENBILICLAW_HUEY_PATH"].endswith("/huey.db")
    assert "huey" not in backend_db


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TransientJobError("temporary"), True),
        (TimeoutError("temporary"), True),
        (PermanentJobError("invalid"), False),
        (ValueError("invalid"), False),
    ],
)
def test_retry_classification_is_explicit(error: Exception, expected: bool) -> None:
    assert classify_retry(error) is expected
