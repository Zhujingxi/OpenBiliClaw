"""Business-state and Huey transport tests for the four vNext jobs."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from huey.api import TaskLock, TaskWrapper

from openbiliclaw.infrastructure.jobs.queue import (
    PRIORITY_INTERACTIVE,
    PRIORITY_SCHEDULED,
    PRIORITY_USER_TRIGGERED,
    build_huey,
)
from openbiliclaw.infrastructure.jobs.tasks import (
    JOB_NAMES,
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

if TYPE_CHECKING:
    from pathlib import Path


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

    def update(
        self,
        run_id: UUID,
        *,
        status: JobRunStatus,
        progress: float,
        error: str | None = None,
    ) -> None:
        self.rows[run_id].update(status=status, progress=progress, error=error)

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
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, UUID, int]] = []

    def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None:
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
