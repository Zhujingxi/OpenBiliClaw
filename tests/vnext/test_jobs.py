"""Business-state and Huey transport tests for the four vNext jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
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
            "worker_id": None,
            "claim_token": None,
            "lease_expires_at": None,
            "retry_not_before": None,
        }
        return run_id, True

    def get(self, run_id: UUID) -> dict[str, object]:
        return self.rows[run_id]

    def claim(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        claim_token: str,
        lease_expires_at: datetime,
        max_attempts: int,
    ) -> bool:
        row = self.rows[run_id]
        retry_not_before = row["retry_not_before"]
        if (
            row["status"] is not JobRunStatus.PENDING
            or int(cast("int", row["attempts"])) >= max_attempts
            or (isinstance(retry_not_before, datetime) and retry_not_before > datetime.now(UTC))
        ):
            return False
        row["status"] = JobRunStatus.RUNNING
        row["attempts"] = int(row["attempts"]) + 1
        row["worker_id"] = worker_id
        row["claim_token"] = claim_token
        row["lease_expires_at"] = lease_expires_at
        row["retry_not_before"] = None
        return True

    def mark_dispatched(self, run_id: UUID) -> None:
        self.rows[run_id]["dispatched_at"] = datetime.now(UTC)

    def pending_undispatched(self) -> tuple[UUID, ...]:
        return tuple(
            run_id
            for run_id, row in self.rows.items()
            if row["status"] is JobRunStatus.PENDING
            and row["dispatched_at"] is None
            and (
                row["retry_not_before"] is None
                or cast("datetime", row["retry_not_before"]) <= datetime.now(UTC)
            )
        )

    def pending(self) -> tuple[UUID, ...]:
        return tuple(
            run_id
            for run_id, row in self.rows.items()
            if row["status"] is JobRunStatus.PENDING
            and (
                row["retry_not_before"] is None
                or cast("datetime", row["retry_not_before"]) <= datetime.now(UTC)
            )
        )

    def guard_running(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        claim_token: str,
        lease_expires_at: datetime,
    ) -> bool:
        row = self.rows[run_id]
        owned = (
            row["status"] is JobRunStatus.RUNNING
            and row["worker_id"] == worker_id
            and row["claim_token"] == claim_token
        )
        if owned:
            row["lease_expires_at"] = lease_expires_at
        return owned

    def checkpoint(
        self,
        run_id: UUID,
        progress: float,
        *,
        worker_id: str,
        claim_token: str,
        lease_expires_at: datetime,
    ) -> bool:
        row = self.rows[run_id]
        if (
            row["status"] is not JobRunStatus.RUNNING
            or row["worker_id"] != worker_id
            or row["claim_token"] != claim_token
        ):
            return False
        row["progress"] = max(float(row["progress"]), progress)
        row["lease_expires_at"] = lease_expires_at
        return True

    def heartbeat(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        claim_token: str,
        lease_expires_at: datetime,
    ) -> bool:
        return self.guard_running(
            run_id,
            worker_id=worker_id,
            claim_token=claim_token,
            lease_expires_at=lease_expires_at,
        )

    def update(
        self,
        run_id: UUID,
        *,
        status: JobRunStatus,
        progress: float,
        error: str | None = None,
        worker_id: str,
        claim_token: str,
        retry_not_before: datetime | None = None,
    ) -> bool:
        if (
            self.rows[run_id].get("worker_id") != worker_id
            or self.rows[run_id].get("claim_token") != claim_token
        ):
            return False
        self.rows[run_id].update(status=status, progress=progress, error=error)
        self.rows[run_id].update(
            worker_id=None,
            claim_token=None,
            lease_expires_at=None,
            retry_not_before=retry_not_before,
        )
        if status is JobRunStatus.PENDING:
            self.rows[run_id]["dispatched_at"] = None
        return True

    def cancel(self, run_id: UUID) -> bool:
        row = self.rows[run_id]
        if row["status"] in {JobRunStatus.SUCCEEDED, JobRunStatus.FAILED}:
            return False
        row["status"] = JobRunStatus.CANCELLED
        row["worker_id"] = None
        row["claim_token"] = None
        row["lease_expires_at"] = None
        row["retry_not_before"] = None
        return True

    def recover_expired(self, *, now: datetime, max_attempts: dict[str, int]) -> tuple[UUID, ...]:
        recovered = []
        for run_id, row in self.rows.items():
            lease = row.get("lease_expires_at")
            if row["status"] is JobRunStatus.RUNNING and (
                lease is None or (isinstance(lease, datetime) and lease <= now)
            ):
                if int(cast("int", row["attempts"])) >= max_attempts[str(row["job_name"])]:
                    row["status"] = JobRunStatus.FAILED
                    row["error"] = "WorkerInterrupted"
                    row["worker_id"] = None
                    row["claim_token"] = None
                    row["lease_expires_at"] = None
                    row["retry_not_before"] = None
                    continue
                row["status"] = JobRunStatus.PENDING
                row["dispatched_at"] = None
                row["worker_id"] = None
                row["claim_token"] = None
                row["lease_expires_at"] = None
                row["retry_not_before"] = None
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
        (feed_replenishment, "feed-replenishment", PRIORITY_SCHEDULED, 2),
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


@pytest.mark.parametrize("priority", [-1, 0, 11, 49, 51, 99, 101, 999999])
def test_schedule_rejects_priorities_outside_the_three_declared_lanes(priority: int) -> None:
    jobs = MemoryJobs()
    service = JobService(lambda: JobUow(jobs), queue=Queue())

    with pytest.raises(ValueError, match="priority"):
        service.schedule("source_sync", idempotency_key="bounded", priority=priority)


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
    assert service.claim(run.id) is None


def test_restart_recovery_requeues_only_application_running_rows() -> None:
    jobs = MemoryJobs()
    queue = Queue()
    service = JobService(lambda: JobUow(jobs), queue=queue)
    run = service.schedule("feed_replenishment", idempotency_key="feed:1")
    assert service.claim(run.id) is not None
    jobs.rows[run.id]["lease_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)

    recovered = service.recover_interrupted()

    assert recovered == (run.id,)
    assert len(queue.enqueued) == 2
    assert service.inspect(run.id).status is JobRunStatus.PENDING


def test_running_cancellation_is_visible_at_checkpoint_and_progress_is_monotonic() -> None:
    jobs = MemoryJobs()
    service = JobService(lambda: JobUow(jobs), queue=Queue())
    run = service.schedule("feed_replenishment", idempotency_key="progress")
    claim_token = service.claim(run.id)
    assert claim_token is not None
    context = JobExecutionContext(service, run.id, claim_token)

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
    first_claim = service.claim(run.id)
    assert first_claim is not None
    first = JobExecutionContext(service, run.id, first_claim)
    first.checkpoint(0.6)
    service.retry(
        run.id,
        TransientJobError("retry"),
        delay_seconds=0,
        claim_token=first_claim,
    )
    second_claim = service.claim(run.id)
    assert second_claim is not None

    JobExecutionContext(service, run.id, second_claim).checkpoint(0.3)

    assert service.inspect(run.id).progress == 0.6


@pytest.mark.parametrize(
    ("job_name", "interval"),
    [
        ("source_sync", 30),
        ("profile_projection", 10),
        ("feed_replenishment", 5),
        ("cleanup", 1440),
    ],
)
def test_periodic_buckets_follow_each_current_typed_setting(job_name: str, interval: int) -> None:
    jobs = MemoryJobs()
    queue = Queue()
    intervals = {
        "source_sync": 30,
        "profile_projection": 10,
        "feed_replenishment": 5,
        "cleanup": 1440,
    }
    service = JobService(
        lambda: JobUow(jobs),
        queue=queue,
        schedule_interval_minutes=lambda name: intervals[name],
    )
    base = datetime(2026, 7, 17, 1, 0, tzinfo=UTC)
    bucket_seconds = interval * 60
    now = datetime.fromtimestamp(
        int(base.timestamp()) // bucket_seconds * bucket_seconds,
        tz=UTC,
    )

    first = service.schedule_periodic(job_name, now=now)  # type: ignore[arg-type]
    duplicate = service.schedule_periodic(  # type: ignore[arg-type]
        job_name, now=now + timedelta(minutes=interval - 1)
    )
    intervals[job_name] = 1
    later = service.schedule_periodic(  # type: ignore[arg-type]
        job_name, now=now + timedelta(minutes=interval)
    )

    assert first.id == duplicate.id
    assert later.id != first.id
    assert len(queue.enqueued) == 2


@pytest.mark.parametrize(
    "job_name",
    ["source_sync", "profile_projection", "feed_replenishment"],
)
def test_ineligible_periodic_product_maintenance_is_a_no_op(job_name: str) -> None:
    jobs = MemoryJobs()
    queue = Queue()
    service = JobService(
        lambda: JobUow(jobs),
        queue=queue,
        periodic_job_eligible=lambda name: name == "cleanup",
    )

    result = service.schedule_periodic(job_name)  # type: ignore[arg-type]

    assert result is None
    assert jobs.rows == {}
    assert queue.enqueued == []


def test_cleanup_periodic_remains_enabled_when_product_maintenance_is_ineligible() -> None:
    jobs = MemoryJobs()
    queue = Queue()
    service = JobService(
        lambda: JobUow(jobs),
        queue=queue,
        periodic_job_eligible=lambda name: name == "cleanup",
    )

    run = service.schedule_periodic("cleanup")

    assert run is not None
    assert run.priority == PRIORITY_SCHEDULED
    assert run.idempotency_key.startswith("cleanup:periodic:")
    assert queue.enqueued == [("cleanup", run.id, PRIORITY_SCHEDULED)]


def test_periodic_eligibility_does_not_affect_explicit_scheduling() -> None:
    jobs = MemoryJobs()
    queue = Queue()
    service = JobService(
        lambda: JobUow(jobs),
        queue=queue,
        periodic_job_eligible=lambda _name: False,
    )

    run = service.schedule("feed_replenishment", idempotency_key="onboarding:continue")

    assert run.priority == PRIORITY_USER_TRIGGERED
    assert queue.enqueued == [("feed_replenishment", run.id, PRIORITY_USER_TRIGGERED)]


@pytest.mark.parametrize("compose_name", ["docker-compose.yml", "docker-compose.prebuilt.yml"])
def test_backend_and_worker_share_vnext_database_but_not_huey(compose_name: str) -> None:
    compose = yaml.safe_load((Path(__file__).parents[2] / compose_name).read_text())
    services: dict[str, Any] = compose["services"]
    backend_db = services["api"]["environment"]["OPENBILICLAW_DATABASE_URL"]
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
