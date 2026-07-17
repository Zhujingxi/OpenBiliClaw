"""Four Huey transports over application-owned durable job state."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import UUID, uuid4

from huey import crontab
from huey.exceptions import CancelExecution
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from openbiliclaw.infrastructure.jobs.queue import (
    PRIORITY_INTERACTIVE,
    PRIORITY_SCHEDULED,
    PRIORITY_USER_TRIGGERED,
    huey,
)

if TYPE_CHECKING:
    from types import TracebackType

JOB_NAMES = ("source_sync", "profile_projection", "feed_replenishment", "cleanup")
JobName = str
logger = logging.getLogger(__name__)


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


class WorkerInterruptedError(RuntimeError):
    """Process shutdown interrupted work that must remain lease-recoverable."""


class JobRunRepository(Protocol):
    def create_or_get(
        self, *, job_name: str, idempotency_key: str, priority: int
    ) -> tuple[UUID, bool]: ...

    def get(self, run_id: UUID) -> JobRunSnapshot | Mapping[str, object]: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> JobRunSnapshot | None: ...

    def claim(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        claim_token: str,
        lease_expires_at: datetime,
        max_attempts: int,
    ) -> bool: ...

    def mark_dispatched(self, run_id: UUID) -> None: ...

    def pending_undispatched(self) -> tuple[UUID, ...]: ...

    def pending(self) -> tuple[UUID, ...]: ...

    def guard_running(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        claim_token: str,
        lease_expires_at: datetime,
    ) -> bool: ...

    def checkpoint(
        self,
        run_id: UUID,
        progress: float,
        *,
        worker_id: str,
        claim_token: str,
        lease_expires_at: datetime,
    ) -> bool: ...

    def heartbeat(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        claim_token: str,
        lease_expires_at: datetime,
    ) -> bool: ...

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
    ) -> bool: ...

    def cancel(self, run_id: UUID) -> bool: ...

    def restart_terminal(self, run_id: UUID) -> bool: ...

    def recover_expired(
        self, *, now: datetime, max_attempts: Mapping[str, int]
    ) -> tuple[UUID, ...]: ...

    def cleanup_finished(self, *, older_than: datetime) -> int: ...

    def list(self, *, limit: int) -> tuple[JobRunSnapshot, ...]: ...

    def successful(self) -> tuple[JobRunSnapshot, ...]: ...

    def acknowledge_success_continuation(self, run_id: UUID) -> bool: ...


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


class GuardedJobUnitOfWork(Protocol):
    """Existing feature transaction exposing the shared job-state repository."""

    job_runs: JobRunRepository


class JobQueue(Protocol):
    def enqueue(self, job_name: str, run_id: UUID, priority: int) -> None: ...


def _snapshot(run_id: UUID, value: JobRunSnapshot | Mapping[str, object]) -> JobRunSnapshot:
    if isinstance(value, JobRunSnapshot):
        return value
    return JobRunSnapshot.model_validate({"id": run_id, **value})


def classify_retry(error: BaseException) -> bool:
    """Classify only known temporary failures as retryable."""

    import httpx
    from pydantic_ai.exceptions import ModelHTTPError

    current: BaseException | None = error
    while current is not None:
        if isinstance(current, PermanentJobError):
            return False
        current = current.__cause__ or current.__context__

    current = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TransientJobError, TimeoutError, ConnectionError)):
            return True
        if isinstance(current, httpx.TransportError):
            return True
        if isinstance(current, httpx.HTTPStatusError):
            status = current.response.status_code
            return status in {408, 409, 425, 429} or status >= 500
        if isinstance(current, ModelHTTPError):
            return current.status_code in {408, 409, 425, 429} or current.status_code >= 500
        status_code = getattr(current, "status_code", None)
        if isinstance(status_code, int) and (
            status_code in {408, 409, 425, 429} or status_code >= 500
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


_DEFAULT_PRIORITY = {job_name: PRIORITY_USER_TRIGGERED for job_name in JOB_NAMES}
_MAX_ATTEMPTS = {"cleanup": 2, "source_sync": 3, "profile_projection": 3, "feed_replenishment": 3}
_DECLARED_PRIORITIES = {
    PRIORITY_INTERACTIVE,
    PRIORITY_SCHEDULED,
    PRIORITY_USER_TRIGGERED,
}
_DEFAULT_INTERVAL_MINUTES: dict[JobName, int] = {
    "source_sync": 30,
    "profile_projection": 10,
    "feed_replenishment": 5,
    "cleanup": 1440,
}


class JobService:
    """Own scheduling, inspection, cancellation, and restart recovery in the app DB."""

    def __init__(
        self,
        uow_factory: Callable[[], JobUnitOfWork],
        *,
        queue: JobQueue,
        schedule_interval_minutes: Callable[[JobName], int] | None = None,
        periodic_job_eligible: Callable[[JobName], bool] | None = None,
        worker_id: str | None = None,
        lease_seconds: float = 120,
    ) -> None:
        resolved_worker_id = worker_id or str(uuid4())
        if not resolved_worker_id.strip():
            raise ValueError("job worker ID cannot be empty")
        if lease_seconds <= 0:
            raise ValueError("job lease must be positive")
        self._uow_factory = uow_factory
        self._queue = queue
        self._worker_id = resolved_worker_id
        self._lease_seconds = lease_seconds
        self._schedule_interval_minutes = (
            schedule_interval_minutes or _DEFAULT_INTERVAL_MINUTES.__getitem__
        )
        self._periodic_job_eligible = periodic_job_eligible or (lambda _job_name: True)
        self._success_callbacks: list[Callable[[JobRunSnapshot], None]] = []

    def register_success_callback(self, callback: Callable[[JobRunSnapshot], None]) -> None:
        """Register an idempotent application continuation for terminal success."""

        self._success_callbacks.append(callback)

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
        if resolved_priority not in _DECLARED_PRIORITIES:
            raise ValueError("job priority must use a declared execution lane")
        durable_key = f"{job_name}:{idempotency_key}"
        with self._uow_factory() as uow:
            run_id, _created = uow.job_runs.create_or_get(
                job_name=job_name,
                idempotency_key=durable_key,
                priority=resolved_priority,
            )
            uow.commit()
        snapshot = self.inspect(run_id)
        with self._uow_factory() as uow:
            ready_to_dispatch = run_id in uow.job_runs.pending_undispatched()
        if ready_to_dispatch:
            self._dispatch(snapshot)
        return self.inspect(run_id)

    def _dispatch(self, snapshot: JobRunSnapshot) -> None:
        """Publish first, then mark; duplicates after a crash are claim-safe."""

        self._queue.enqueue(snapshot.job_name, snapshot.id, snapshot.priority)
        with self._uow_factory() as uow:
            uow.job_runs.mark_dispatched(snapshot.id)
            uow.commit()

    def reconcile_pending_dispatches(self, *, include_dispatched: bool = False) -> tuple[UUID, ...]:
        """Republish durable pending rows, including marked handoffs during startup."""

        with self._uow_factory() as uow:
            pending = (
                uow.job_runs.pending()
                if include_dispatched
                else uow.job_runs.pending_undispatched()
            )
        dispatched: list[UUID] = []
        for run_id in pending:
            snapshot = self.inspect(run_id)
            self._dispatch(snapshot)
            dispatched.append(run_id)
        return tuple(dispatched)

    def schedule_periodic(
        self, job_name: JobName, *, now: datetime | None = None
    ) -> JobRunSnapshot | None:
        if job_name not in JOB_NAMES:
            raise ValueError(f"unknown job name: {job_name}")
        if not self._periodic_job_eligible(job_name):
            return None
        resolved_now = now or datetime.now(UTC)
        interval = self._schedule_interval_minutes(job_name)
        if interval < 1:
            raise ValueError(f"{job_name} interval must be positive")
        bucket = int(resolved_now.timestamp() // 60) // interval
        return self.schedule(
            job_name,
            idempotency_key=f"periodic:{bucket}",
            priority=PRIORITY_SCHEDULED,
        )

    def inspect(self, run_id: UUID) -> JobRunSnapshot:
        with self._uow_factory() as uow:
            value = uow.job_runs.get(run_id)
        return _snapshot(run_id, value)

    def find_by_idempotency_key(self, idempotency_key: str) -> JobRunSnapshot | None:
        """Find one durable run by its full application-owned idempotency key."""

        with self._uow_factory() as uow:
            return uow.job_runs.get_by_idempotency_key(idempotency_key)

    def list(self, *, limit: int = 100) -> tuple[JobRunSnapshot, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("invalid job page size")
        with self._uow_factory() as uow:
            return uow.job_runs.list(limit=limit)

    def cancel(self, run_id: UUID) -> JobRunSnapshot:
        with self._uow_factory() as uow:
            uow.job_runs.cancel(run_id)
            uow.commit()
        return self.inspect(run_id)

    def restart_terminal(self, run_id: object) -> JobRunSnapshot:
        """Explicitly resume a failed/cancelled durable run without duplicating its identity."""

        if not isinstance(run_id, UUID):
            raise TypeError("job run ID must be a UUID")
        with self._uow_factory() as uow:
            restarted = uow.job_runs.restart_terminal(run_id)
            uow.commit()
        snapshot = self.inspect(run_id)
        if restarted:
            self._dispatch(snapshot)
        return self.inspect(run_id)

    def claim(self, run_id: UUID) -> str | None:
        snapshot = self.inspect(run_id)
        claim_token = str(uuid4())
        with self._uow_factory() as uow:
            claimed = uow.job_runs.claim(
                run_id,
                worker_id=self._worker_id,
                claim_token=claim_token,
                lease_expires_at=self._lease_deadline(),
                max_attempts=_MAX_ATTEMPTS[snapshot.job_name],
            )
            uow.commit()
        return claim_token if claimed else None

    def checkpoint(self, run_id: UUID, progress: float, *, claim_token: str) -> JobRunSnapshot:
        if not 0 <= progress <= 1:
            raise ValueError("job progress must be between zero and one")
        with self._uow_factory() as uow:
            running = uow.job_runs.checkpoint(
                run_id,
                progress,
                worker_id=self._worker_id,
                claim_token=claim_token,
                lease_expires_at=self._lease_deadline(),
            )
            uow.commit()
        snapshot = self.inspect(run_id)
        if not running:
            if snapshot.status is JobRunStatus.CANCELLED:
                raise JobCancelledError(f"job was cancelled: {run_id}")
            raise RuntimeError(f"job is not running: {run_id}")
        return snapshot

    def succeed(self, run_id: UUID, *, claim_token: str) -> None:
        if not self._update(
            run_id,
            status=JobRunStatus.SUCCEEDED,
            progress=1.0,
            claim_token=claim_token,
        ):
            return
        self._continue_success(self.inspect(run_id))

    def fail(self, run_id: UUID, error: BaseException, *, claim_token: str) -> None:
        self._update(
            run_id,
            status=JobRunStatus.FAILED,
            progress=self.inspect(run_id).progress,
            error=type(error).__name__,
            claim_token=claim_token,
        )

    def retry(
        self,
        run_id: UUID,
        error: BaseException,
        *,
        delay_seconds: float,
        claim_token: str,
    ) -> bool:
        snapshot = self.inspect(run_id)
        if snapshot.attempts >= _MAX_ATTEMPTS[snapshot.job_name]:
            return False
        return self._update(
            run_id,
            status=JobRunStatus.PENDING,
            progress=snapshot.progress,
            error=type(error).__name__,
            claim_token=claim_token,
            retry_not_before=datetime.now(UTC) + timedelta(seconds=max(0, delay_seconds)),
        )

    def _update(
        self,
        run_id: UUID,
        *,
        status: JobRunStatus,
        progress: float,
        error: str | None = None,
        claim_token: str,
        retry_not_before: datetime | None = None,
    ) -> bool:
        with self._uow_factory() as uow:
            updated = uow.job_runs.update(
                run_id,
                status=status,
                progress=progress,
                error=error,
                worker_id=self._worker_id,
                claim_token=claim_token,
                retry_not_before=retry_not_before,
            )
            uow.commit()
        return updated

    def recover_interrupted(self, *, now: datetime | None = None) -> tuple[UUID, ...]:
        """Worker-startup recovery plus crash-window reconciliation."""

        with self._uow_factory() as uow:
            recovered = uow.job_runs.recover_expired(
                now=now or datetime.now(UTC), max_attempts=_MAX_ATTEMPTS
            )
            uow.commit()
        self.reconcile_pending_dispatches(include_dispatched=True)
        self.reconcile_successful()
        return recovered

    def recover_expired_leases(self, *, now: datetime | None = None) -> tuple[UUID, ...]:
        """Recover newly expired runs and replay unacknowledged success continuations.

        Unlike startup reconciliation, this does not republish unrelated pending Huey
        retries and therefore preserves their configured retry delay.
        """

        with self._uow_factory() as uow:
            recovered = uow.job_runs.recover_expired(
                now=now or datetime.now(UTC), max_attempts=_MAX_ATTEMPTS
            )
            uow.commit()
        for run_id in recovered:
            self._dispatch(self.inspect(run_id))
        self.reconcile_pending_dispatches()
        self.reconcile_successful()
        return recovered

    def heartbeat(self, run_id: UUID, *, claim_token: str) -> bool:
        """Extend this worker's owned lease without changing business progress."""

        with self._uow_factory() as uow:
            active = uow.job_runs.heartbeat(
                run_id,
                worker_id=self._worker_id,
                claim_token=claim_token,
                lease_expires_at=self._lease_deadline(),
            )
            uow.commit()
        return active

    def guard(self, repository: JobRunRepository, run_id: UUID, *, claim_token: str) -> bool:
        """Refresh and verify ownership inside an existing feature transaction."""

        return repository.guard_running(
            run_id,
            worker_id=self._worker_id,
            claim_token=claim_token,
            lease_expires_at=self._lease_deadline(),
        )

    @property
    def heartbeat_interval_seconds(self) -> float:
        return max(0.1, min(30.0, self._lease_seconds / 3))

    @property
    def recovery_interval_seconds(self) -> float:
        """Bound expired-run and successful-continuation recovery latency."""

        return self.heartbeat_interval_seconds

    def _lease_deadline(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self._lease_seconds)

    def reconcile_successful(self) -> None:
        """Replay unacknowledged terminal-success continuations in startup or live sweeps."""

        if not self._success_callbacks:
            return
        with self._uow_factory() as uow:
            successful = uow.job_runs.successful()
        for snapshot in successful:
            self._continue_success(snapshot)

    def _continue_success(self, snapshot: JobRunSnapshot) -> None:
        """Run every idempotent callback before durably acknowledging the continuation."""

        if not self._success_callbacks:
            return
        for callback in self._success_callbacks:
            try:
                callback(snapshot)
            except Exception as error:
                logger.warning("job success continuation deferred (%s)", type(error).__name__)
                return
        try:
            with self._uow_factory() as uow:
                uow.job_runs.acknowledge_success_continuation(snapshot.id)
                uow.commit()
        except Exception as error:
            logger.warning("job success acknowledgement deferred (%s)", type(error).__name__)

    def cleanup_finished(
        self,
        *,
        retention_days: int = 30,
        transaction_guard: Callable[[object], None] | None = None,
    ) -> int:
        if retention_days < 1:
            raise ValueError("job retention must be at least one day")
        with self._uow_factory() as uow:
            if transaction_guard is not None:
                transaction_guard(uow)
            removed = uow.job_runs.cleanup_finished(
                older_than=datetime.now(UTC) - timedelta(days=retention_days)
            )
            uow.commit()
        return removed


class JobExecutionContext:
    """Application-state checkpoint used for progress and cooperative cancellation."""

    def __init__(self, service: JobService, run_id: UUID, claim_token: str) -> None:
        self._service = service
        self.run_id = run_id
        self.claim_token = claim_token

    def checkpoint(self, progress: float) -> JobRunSnapshot:
        return self._service.checkpoint(self.run_id, progress, claim_token=self.claim_token)

    def guard(self, uow: object) -> None:
        """Linearize cancellation with feature writes in their existing transaction."""

        guarded = cast("GuardedJobUnitOfWork", uow)
        if self._service.guard(guarded.job_runs, self.run_id, claim_token=self.claim_token):
            return
        snapshot = _snapshot(self.run_id, guarded.job_runs.get(self.run_id))
        if snapshot.status is JobRunStatus.CANCELLED:
            raise JobCancelledError(f"job was cancelled: {self.run_id}")
        raise RuntimeError(f"job is not running: {self.run_id}")


JobHandler = Callable[[UUID, JobExecutionContext], None | Awaitable[None]]
AsyncJobRunner = Callable[[Awaitable[None]], None]
_runtime_lock = Lock()
_service: JobService | None = None
_handlers: dict[str, JobHandler] = {}
_async_job_runner: AsyncJobRunner | None = None


class _JobLeaseHeartbeat:
    """Keep ownership live while a handler is blocked in connector or AI I/O."""

    def __init__(self, service: JobService, run_id: UUID, claim_token: str) -> None:
        self._service = service
        self._run_id = run_id
        self._claim_token = claim_token
        self._stop = Event()
        self._thread = Thread(target=self._run, name=f"job-heartbeat-{run_id}", daemon=True)

    def __enter__(self) -> _JobLeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self._service.heartbeat_interval_seconds + 1)

    def _run(self) -> None:
        interval = self._service.heartbeat_interval_seconds
        while not self._stop.wait(interval):
            try:
                if not self._service.heartbeat(self._run_id, claim_token=self._claim_token):
                    return
            except Exception as error:
                logger.warning("job lease heartbeat deferred (%s)", type(error).__name__)


def configure_job_runtime(
    service: JobService,
    handlers: Mapping[str, JobHandler],
    *,
    async_job_runner: AsyncJobRunner | None = None,
) -> None:
    """Inject application orchestration into the worker process at composition time."""

    unknown = set(handlers) - set(JOB_NAMES)
    if unknown:
        raise ValueError(f"unknown job handlers: {sorted(unknown)}")
    with _runtime_lock:
        global _service, _handlers, _async_job_runner  # noqa: PLW0603
        _service = service
        _handlers = dict(handlers)
        _async_job_runner = async_job_runner


def _run_job(job_name: str, run_id: str | None, task: Any | None) -> None:
    service = _service
    if service is None:
        raise PermanentJobError("job runtime is not configured")
    if run_id is None:
        service.schedule_periodic(job_name)
        return
    resolved_id = UUID(run_id)
    claim_token = service.claim(resolved_id)
    if claim_token is None:
        return
    handler = _handlers.get(job_name)
    if handler is None:
        error = PermanentJobError(f"job handler is not configured: {job_name}")
        service.fail(resolved_id, error, claim_token=claim_token)
        raise CancelExecution(retry=False)
    try:
        with _JobLeaseHeartbeat(service, resolved_id, claim_token):
            context = JobExecutionContext(service, resolved_id, claim_token)
            context.checkpoint(0.01)
            result = handler(resolved_id, context)
            if inspect.isawaitable(result):
                runner = _async_job_runner
                if runner is None:
                    asyncio.run(_await_handler(result))
                else:
                    runner(result)
            context.checkpoint(0.99)
        service.succeed(resolved_id, claim_token=claim_token)
    except WorkerInterruptedError as error:
        # Do not terminalize incomplete work during process teardown. The
        # running row remains fenced by its lease and is recovered by the next
        # worker lifecycle sweep.
        raise CancelExecution(retry=False) from error
    except JobCancelledError as error:
        raise CancelExecution(retry=False) from error
    except BaseException as error:
        retries_remaining = int(getattr(task, "retries", 0))
        retry_delay = float(getattr(task, "retry_delay", 0) or 0)
        if (
            classify_retry(error)
            and retries_remaining > 0
            and service.retry(
                resolved_id,
                error,
                delay_seconds=retry_delay,
                claim_token=claim_token,
            )
        ):
            raise
        service.fail(resolved_id, error, claim_token=claim_token)
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
    crontab(minute="*"),
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
    crontab(minute="*"),
    retries=2,
    retry_delay=30,
    priority=PRIORITY_SCHEDULED,
    context=True,
    name="feed_replenishment",
)
@huey.lock_task("feed-replenishment")  # type: ignore[untyped-decorator]
def feed_replenishment(run_id: str | None = None, *, task: Any | None = None) -> None:
    _run_job("feed_replenishment", run_id, task)


@huey.periodic_task(  # type: ignore[untyped-decorator]
    crontab(minute="*"),
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
    "WorkerInterruptedError",
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
