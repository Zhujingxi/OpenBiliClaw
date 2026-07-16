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


class TransientJobError(RuntimeError):
    """An infrastructure failure that is safe to retry within the bounded policy."""


class PermanentJobError(RuntimeError):
    """Invalid or unsupported work that retrying cannot repair."""


class JobRunRepository(Protocol):
    def create_or_get(
        self, *, job_name: str, idempotency_key: str, priority: int
    ) -> tuple[UUID, bool]: ...

    def get(self, run_id: UUID) -> JobRunSnapshot | Mapping[str, object]: ...

    def claim(self, run_id: UUID) -> bool: ...

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

    def __init__(self, uow_factory: Callable[[], JobUnitOfWork], *, queue: JobQueue) -> None:
        self._uow_factory = uow_factory
        self._queue = queue

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
            run_id, created = uow.job_runs.create_or_get(
                job_name=job_name,
                idempotency_key=durable_key,
                priority=resolved_priority,
            )
            uow.commit()
        if created:
            self._queue.enqueue(job_name, run_id, resolved_priority)
        return self.inspect(run_id)

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
            snapshots = tuple(_snapshot(run_id, uow.job_runs.get(run_id)) for run_id in recovered)
            uow.commit()
        for snapshot in snapshots:
            self._queue.enqueue(snapshot.job_name, snapshot.id, snapshot.priority)
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


JobHandler = Callable[[UUID], None | Awaitable[None]]
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
        bucket = datetime.now(UTC).strftime("%Y%m%d%H%M")
        service.schedule(job_name, idempotency_key=f"periodic:{bucket}")
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
        result = handler(resolved_id)
        if inspect.isawaitable(result):
            asyncio.run(_await_handler(result))
        service.succeed(resolved_id)
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
    crontab(minute="*/30"),
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

    def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None:
        try:
            wrapper = _TASKS[job_name]
        except KeyError as exc:
            raise ValueError(f"unknown job name: {job_name}") from exc
        wrapper.schedule((str(run_id),), priority=priority, id=str(run_id))


__all__ = [
    "JOB_NAMES",
    "HueyJobQueue",
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
