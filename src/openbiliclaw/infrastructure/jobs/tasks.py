"""Four Huey transports over application-owned durable job state."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from huey import crontab
from huey.exceptions import CancelExecution
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from openbiliclaw.infrastructure.jobs.queue import (
    PRIORITY_SCHEDULED,
    PRIORITY_USER_TRIGGERED,
    huey,
)

if TYPE_CHECKING:
    from types import TracebackType

JOB_NAMES = ("source_sync", "profile_projection", "feed_replenishment", "cleanup")
JobName = str


class JobRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobRunSnapshot(BaseModel):
    """Application DB read model; Huey Result is intentionally absent."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: UUID
    job_name: str
    idempotency_key: str
    status: JobRunStatus
    priority: int
    progress: float = Field(ge=0, le=1)
    attempts: int = Field(default=0, ge=0)
    error: str | None = None
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    dispatched_at: AwareDatetime | None = None


class TransientJobError(RuntimeError):
    """An infrastructure failure that is safe to retry within the bounded policy."""


class PermanentJobError(RuntimeError):
    """Invalid or unsupported work that retrying cannot repair."""


class JobCancelledError(RuntimeError):
    """Cooperative signal raised when application state cancels in-flight work."""


class JobRunRepository(Protocol):
    def create_or_get(
        self, *, job_name: str, idempotency_key: str, priority: int
    ) -> tuple[UUID, bool]: ...

    def get(self, run_id: UUID) -> JobRunSnapshot | Mapping[str, object]: ...

    def claim(self, run_id: UUID) -> bool: ...

    def mark_dispatched(self, run_id: UUID) -> None: ...

    def pending_undispatched(self) -> tuple[UUID, ...]: ...

    def checkpoint(self, run_id: UUID, progress: float) -> bool: ...

    def update(
        self,
        run_id: UUID,
        *,
        status: JobRunStatus,
        progress: float,
        error: str | None = None,
    ) -> None: ...

    def cancel(self, run_id: UUID) -> bool: ...

    def recover_running(self) -> tuple[UUID, ...]: ...

    def cleanup_finished(self, *, older_than: datetime) -> int: ...


class JobUnitOfWork(Protocol):
    job_runs: JobRunRepository

    def __enter__(self) -> JobUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class JobQueue(Protocol):
    def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None: ...


def _snapshot(run_id: UUID, value: JobRunSnapshot | Mapping[str, object]) -> JobRunSnapshot:
    if isinstance(value, JobRunSnapshot):
        return value
    return JobRunSnapshot.model_validate({"id": run_id, **value})


def classify_retry(error: BaseException) -> bool:
    """Classify only known temporary failures as retryable."""

    return isinstance(error, (TransientJobError, TimeoutError, ConnectionError))


_DEFAULT_PRIORITY = {
    "source_sync": PRIORITY_SCHEDULED,
    "profile_projection": PRIORITY_SCHEDULED,
    "feed_replenishment": PRIORITY_USER_TRIGGERED,
    "cleanup": PRIORITY_SCHEDULED,
}


class JobService:
    """Own scheduling, inspection, cancellation, and restart recovery in the app DB."""

    def __init__(
        self,
        uow_factory: Callable[[], JobUnitOfWork],
        *,
        queue: JobQueue,
        source_sync_interval_minutes: Callable[[], int] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._queue = queue
        self._source_sync_interval_minutes = source_sync_interval_minutes or (lambda: 30)

    def schedule(
        self,
        job_name: JobName,
        *,
        idempotency_key: str,
        priority: int | None = None,
    ) -> JobRunSnapshot:
        if job_name not in JOB_NAMES:
            raise ValueError(f"unknown job name: {job_name}")
        if not idempotency_key.strip():
            raise ValueError("job idempotency key cannot be empty")
        resolved_priority = _DEFAULT_PRIORITY[job_name] if priority is None else priority
        durable_key = f"{job_name}:{idempotency_key}"
        with self._uow_factory() as uow:
            run_id, _created = uow.job_runs.create_or_get(
                job_name=job_name,
                idempotency_key=durable_key,
                priority=resolved_priority,
            )
            uow.commit()
        snapshot = self.inspect(run_id)
        if snapshot.status is JobRunStatus.PENDING and snapshot.dispatched_at is None:
            self._dispatch(snapshot)
        return self.inspect(run_id)

    def _dispatch(self, snapshot: JobRunSnapshot) -> None:
        """Publish first, then mark; duplicates after a crash are claim-safe."""

        self._queue.enqueue(snapshot.job_name, snapshot.id, snapshot.priority)
        with self._uow_factory() as uow:
            uow.job_runs.mark_dispatched(snapshot.id)
            uow.commit()

    def reconcile_pending_dispatches(self) -> tuple[UUID, ...]:
        """Republish every durable pending row that has no successful handoff marker."""

        with self._uow_factory() as uow:
            pending = uow.job_runs.pending_undispatched()
        dispatched: list[UUID] = []
        for run_id in pending:
            snapshot = self.inspect(run_id)
            self._dispatch(snapshot)
            dispatched.append(run_id)
        return tuple(dispatched)

    def schedule_periodic(
        self, job_name: JobName, *, now: datetime | None = None
    ) -> JobRunSnapshot:
        resolved_now = now or datetime.now(UTC)
        if job_name == "source_sync":
            interval = self._source_sync_interval_minutes()
            if interval < 1:
                raise ValueError("source sync interval must be positive")
            bucket = int(resolved_now.timestamp() // 60) // interval
        else:
            bucket = int(resolved_now.timestamp() // 60)
        return self.schedule(job_name, idempotency_key=f"periodic:{bucket}")

    def inspect(self, run_id: UUID) -> JobRunSnapshot:
        with self._uow_factory() as uow:
            value = uow.job_runs.get(run_id)
        return _snapshot(run_id, value)

    def cancel(self, run_id: UUID) -> JobRunSnapshot:
        with self._uow_factory() as uow:
            uow.job_runs.cancel(run_id)
            uow.commit()
        return self.inspect(run_id)

    def claim(self, run_id: UUID) -> bool:
        with self._uow_factory() as uow:
            claimed = uow.job_runs.claim(run_id)
            uow.commit()
        return claimed

    def checkpoint(self, run_id: UUID, progress: float) -> JobRunSnapshot:
        if not 0 <= progress <= 1:
            raise ValueError("job progress must be between zero and one")
        with self._uow_factory() as uow:
            running = uow.job_runs.checkpoint(run_id, progress)
            uow.commit()
        snapshot = self.inspect(run_id)
        if not running:
            if snapshot.status is JobRunStatus.CANCELLED:
                raise JobCancelledError(f"job was cancelled: {run_id}")
            raise RuntimeError(f"job is not running: {run_id}")
        return snapshot

    def succeed(self, run_id: UUID) -> None:
        self._update(run_id, status=JobRunStatus.SUCCEEDED, progress=1.0)

    def fail(self, run_id: UUID, error: BaseException) -> None:
        self._update(
            run_id,
            status=JobRunStatus.FAILED,
            progress=self.inspect(run_id).progress,
            error=type(error).__name__,
        )

    def retry(self, run_id: UUID, error: BaseException) -> None:
        self._update(
            run_id,
            status=JobRunStatus.PENDING,
            progress=self.inspect(run_id).progress,
            error=type(error).__name__,
        )

    def _update(
        self,
        run_id: UUID,
        *,
        status: JobRunStatus,
        progress: float,
        error: str | None = None,
    ) -> None:
        with self._uow_factory() as uow:
            uow.job_runs.update(
                run_id,
                status=status,
                progress=progress,
                error=error,
            )
            uow.commit()

    def recover_interrupted(self) -> tuple[UUID, ...]:
        with self._uow_factory() as uow:
            recovered = uow.job_runs.recover_running()
            uow.commit()
        self.reconcile_pending_dispatches()
        return recovered

    def cleanup_finished(self, *, retention_days: int = 30) -> int:
        if retention_days < 1:
            raise ValueError("job retention must be at least one day")
        with self._uow_factory() as uow:
            removed = uow.job_runs.cleanup_finished(
                older_than=datetime.now(UTC) - timedelta(days=retention_days)
            )
            uow.commit()
        return removed


class JobExecutionContext:
    """Application-state checkpoint used for progress and cooperative cancellation."""

    def __init__(self, service: JobService, run_id: UUID) -> None:
        self._service = service
        self.run_id = run_id

    def checkpoint(self, progress: float) -> JobRunSnapshot:
        return self._service.checkpoint(self.run_id, progress)


JobHandler = Callable[[UUID, JobExecutionContext], None | Awaitable[None]]
_runtime_lock = Lock()
_service: JobService | None = None
_handlers: dict[str, JobHandler] = {}


def configure_job_runtime(service: JobService, handlers: Mapping[str, JobHandler]) -> None:
    """Inject application orchestration into the worker process at composition time."""

    unknown = set(handlers) - set(JOB_NAMES)
    if unknown:
        raise ValueError(f"unknown job handlers: {sorted(unknown)}")
    with _runtime_lock:
        global _service, _handlers  # noqa: PLW0603 - explicit worker composition seam
        _service = service
        _handlers = dict(handlers)


def _run_job(job_name: str, run_id: str | None, task: Any | None) -> None:
    service = _service
    if service is None:
        raise PermanentJobError("job runtime is not configured")
    if run_id is None:
        service.schedule_periodic(job_name)
        return
    resolved_id = UUID(run_id)
    if not service.claim(resolved_id):
        return
    handler = _handlers.get(job_name)
    if handler is None:
        error = PermanentJobError(f"job handler is not configured: {job_name}")
        service.fail(resolved_id, error)
        raise CancelExecution(retry=False)
    try:
        context = JobExecutionContext(service, resolved_id)
        context.checkpoint(0.01)
        result = handler(resolved_id, context)
        if inspect.isawaitable(result):
            asyncio.run(_await_handler(result))
        context.checkpoint(0.99)
        service.succeed(resolved_id)
    except JobCancelledError as error:
        raise CancelExecution(retry=False) from error
    except BaseException as error:
        retries_remaining = int(getattr(task, "retries", 0))
        if classify_retry(error) and retries_remaining > 0:
            service.retry(resolved_id, error)
            raise
        service.fail(resolved_id, error)
        raise CancelExecution(retry=False) from error


async def _await_handler(result: Awaitable[None]) -> None:
    await result


# All schedules are deliberately bounded and serve only as transport triggers. Their durable
# idempotency buckets and outcomes live in job_runs, not Huey's periodic/result storage.
@huey.periodic_task(  # type: ignore[untyped-decorator]
    crontab(minute="*"),
    retries=2,
    retry_delay=30,
    priority=PRIORITY_SCHEDULED,
    context=True,
    name="source_sync",
)
@huey.lock_task("source-sync")  # type: ignore[untyped-decorator]
def source_sync(run_id: str | None = None, *, task: Any | None = None) -> None:
    _run_job("source_sync", run_id, task)


@huey.periodic_task(  # type: ignore[untyped-decorator]
    crontab(minute="*/10"),
    retries=2,
    retry_delay=30,
    priority=PRIORITY_SCHEDULED,
    context=True,
    name="profile_projection",
)
@huey.lock_task("profile-projection")  # type: ignore[untyped-decorator]
def profile_projection(run_id: str | None = None, *, task: Any | None = None) -> None:
    _run_job("profile_projection", run_id, task)


@huey.periodic_task(  # type: ignore[untyped-decorator]
    crontab(minute="*/5"),
    retries=2,
    retry_delay=30,
    priority=PRIORITY_USER_TRIGGERED,
    context=True,
    name="feed_replenishment",
)
@huey.lock_task("feed-replenishment")  # type: ignore[untyped-decorator]
def feed_replenishment(run_id: str | None = None, *, task: Any | None = None) -> None:
    _run_job("feed_replenishment", run_id, task)


@huey.periodic_task(  # type: ignore[untyped-decorator]
    crontab(minute="0", hour="3"),
    retries=1,
    retry_delay=60,
    priority=PRIORITY_SCHEDULED,
    context=True,
    name="cleanup",
)
@huey.lock_task("cleanup")  # type: ignore[untyped-decorator]
def cleanup(run_id: str | None = None, *, task: Any | None = None) -> None:
    _run_job("cleanup", run_id, task)


_TASKS = {
    "source_sync": source_sync,
    "profile_projection": profile_projection,
    "feed_replenishment": feed_replenishment,
    "cleanup": cleanup,
}


class HueyJobQueue:
    """Transport adapter that never returns or exposes Huey result handles."""

    def __init__(self, tasks: Mapping[str, Any] | None = None) -> None:
        self._tasks = dict(tasks or _TASKS)

    def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None:
        try:
            wrapper = self._tasks[job_name]
        except KeyError as exc:
            raise ValueError(f"unknown job name: {job_name}") from exc
        wrapper(str(run_id), priority=priority)


__all__ = [
    "JOB_NAMES",
    "HueyJobQueue",
    "JobCancelledError",
    "JobExecutionContext",
    "JobRunSnapshot",
    "JobRunStatus",
    "JobService",
    "PermanentJobError",
    "TransientJobError",
    "classify_retry",
    "cleanup",
    "configure_job_runtime",
    "feed_replenishment",
    "profile_projection",
    "source_sync",
]
