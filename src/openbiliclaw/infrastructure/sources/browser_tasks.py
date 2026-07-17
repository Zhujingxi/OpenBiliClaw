"""Durable browser source-task persistence and awaiting transport."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update

from openbiliclaw.features.sources.domain import (
    ClaimedSourceTask,
    SourceId,
    SourceOperation,
    SourceTaskCompletion,
    SourceTaskRequest,
    SourceTaskSnapshot,
    SourceTaskStatus,
)
from openbiliclaw.features.sources.service import (
    AbandonedSourceTaskError,
    CancelledSourceTaskError,
    SourceTaskCompletionConflictError,
    StaleSourceTaskLeaseError,
)
from openbiliclaw.infrastructure.database.models import SourceTaskModel

if TYPE_CHECKING:
    from pydantic import JsonValue
    from sqlalchemy.orm import Session

    from openbiliclaw.features.sources.service import SourceTaskService


logger = logging.getLogger(__name__)
_CLEANUP_SCHEDULING_GRACE_SECONDS = 0.25
_ACTIONABLE_STATUSES = (SourceTaskStatus.PENDING.value, SourceTaskStatus.IN_PROGRESS.value)


class SQLAlchemyBrowserTaskRepository:
    """Atomically claim expired/pending tasks and preserve idempotent completions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_pending(
        self,
        request: SourceTaskRequest,
        *,
        task_id: UUID,
        request_deadline_at: datetime,
        now: datetime,
    ) -> UUID:
        dumped = request.model_dump(mode="json")
        payload = dumped["payload"]
        if not isinstance(payload, dict):
            raise TypeError("source task payload must serialize as an object")
        self._session.add(
            SourceTaskModel(
                id=str(task_id),
                source_id=request.source_id,
                operation=request.operation.value,
                status="pending",
                request_payload=payload,
                result_payload=None,
                lease_token=None,
                lease_expires_at=None,
                request_deadline_at=request_deadline_at,
                created_at=now,
                updated_at=now,
            )
        )
        return task_id

    def claim(
        self,
        *,
        source_id: str,
        allowed_operations: frozenset[str],
        lease_token: str,
        lease_seconds: int,
    ) -> ClaimedSourceTask | None:
        if not allowed_operations:
            return None
        self._abandon_expired(source_id=source_id)
        claimable = and_(
            _database_time_is_before(SourceTaskModel.request_deadline_at),
            or_(
                SourceTaskModel.status == "pending",
                and_(
                    SourceTaskModel.status == "in_progress",
                    _database_time_is_at_or_after(SourceTaskModel.lease_expires_at),
                ),
            ),
        )
        for _attempt in range(3):
            candidate_id = self._session.scalar(
                select(SourceTaskModel.id)
                .where(
                    SourceTaskModel.source_id == source_id,
                    SourceTaskModel.operation.in_(allowed_operations),
                    claimable,
                )
                .order_by(SourceTaskModel.created_at, SourceTaskModel.id)
                .limit(1)
            )
            if candidate_id is None:
                return None
            row = self._session.scalar(
                update(SourceTaskModel)
                .where(SourceTaskModel.id == candidate_id, claimable)
                .values(
                    status="in_progress",
                    lease_token=lease_token,
                    lease_expires_at=_database_time_plus(seconds=lease_seconds),
                    updated_at=_database_time(),
                )
                .returning(SourceTaskModel)
            )
            if row is not None:
                if row.lease_expires_at is None:
                    raise RuntimeError("claimed source task has no lease deadline")
                return ClaimedSourceTask.model_validate(
                    {
                        "id": UUID(row.id),
                        "source_id": SourceId(row.source_id),
                        "payload": row.request_payload,
                        "lease_token": lease_token,
                        "lease_expires_at": _aware(row.lease_expires_at),
                        "request_deadline_at": _aware(row.request_deadline_at),
                    }
                )
            self._session.expire_all()
        return None

    def complete(
        self,
        *,
        task_id: UUID,
        lease_token: str,
        result: dict[str, JsonValue],
    ) -> SourceTaskCompletion:
        row = self._session.scalar(
            update(SourceTaskModel)
            .where(
                SourceTaskModel.id == str(task_id),
                SourceTaskModel.status == "in_progress",
                SourceTaskModel.lease_token == lease_token,
                _database_time_is_before(SourceTaskModel.lease_expires_at),
                _database_time_is_before(SourceTaskModel.request_deadline_at),
            )
            .values(
                status="completed",
                result_payload=result,
                lease_expires_at=None,
                updated_at=_database_time(),
            )
            .returning(SourceTaskModel)
        )
        if row is not None:
            return SourceTaskCompletion(
                id=task_id,
                completed_at=_aware(row.updated_at),
                idempotent=False,
            )
        self._abandon_expired(task_id=task_id)
        self._session.expire_all()
        row = self._session.get(SourceTaskModel, str(task_id))
        if row is None:
            raise LookupError(f"source task does not exist: {task_id}")
        if row.status == SourceTaskStatus.CANCELLED:
            raise CancelledSourceTaskError("source task was cancelled")
        if row.status == SourceTaskStatus.ABANDONED:
            raise AbandonedSourceTaskError("source task request deadline expired")
        if row.status == "completed":
            if row.lease_token != lease_token:
                raise StaleSourceTaskLeaseError("source task completion lease is stale")
            if row.result_payload != result:
                raise SourceTaskCompletionConflictError(
                    "source task was already completed with a different result"
                )
            return SourceTaskCompletion(
                id=task_id, completed_at=_aware(row.updated_at), idempotent=True
            )
        raise StaleSourceTaskLeaseError("source task completion lease is stale")

    def fail(
        self,
        *,
        task_id: UUID,
        lease_token: str,
        failure: dict[str, JsonValue],
    ) -> SourceTaskCompletion:
        row = self._session.scalar(
            update(SourceTaskModel)
            .where(
                SourceTaskModel.id == str(task_id),
                SourceTaskModel.status == SourceTaskStatus.IN_PROGRESS.value,
                SourceTaskModel.lease_token == lease_token,
                _database_time_is_before(SourceTaskModel.lease_expires_at),
                _database_time_is_before(SourceTaskModel.request_deadline_at),
            )
            .values(
                status=SourceTaskStatus.FAILED.value,
                result_payload=failure,
                lease_expires_at=None,
                updated_at=_database_time(),
            )
            .returning(SourceTaskModel)
        )
        if row is not None:
            return SourceTaskCompletion(
                id=task_id,
                completed_at=_aware(row.updated_at),
                idempotent=False,
            )
        self._abandon_expired(task_id=task_id)
        self._session.expire_all()
        row = self._session.get(SourceTaskModel, str(task_id))
        if row is None:
            raise LookupError(f"source task does not exist: {task_id}")
        if row.status == SourceTaskStatus.CANCELLED.value:
            raise CancelledSourceTaskError("source task was cancelled")
        if row.status == SourceTaskStatus.ABANDONED.value:
            raise AbandonedSourceTaskError("source task request deadline expired")
        if row.status == SourceTaskStatus.FAILED.value:
            if row.lease_token != lease_token:
                raise StaleSourceTaskLeaseError("source task completion lease is stale")
            if row.result_payload != failure:
                raise SourceTaskCompletionConflictError(
                    "source task was already failed with a different classification"
                )
            return SourceTaskCompletion(
                id=task_id,
                completed_at=_aware(row.updated_at),
                idempotent=True,
            )
        if row.status == SourceTaskStatus.COMPLETED.value:
            raise SourceTaskCompletionConflictError(
                "source task was already completed successfully"
            )
        raise StaleSourceTaskLeaseError("source task completion lease is stale")

    def cancel(self, task_id: UUID) -> SourceTaskSnapshot:
        row = self._session.scalar(
            update(SourceTaskModel)
            .where(
                SourceTaskModel.id == str(task_id),
                SourceTaskModel.status.in_(("pending", "in_progress")),
            )
            .values(
                status=SourceTaskStatus.CANCELLED.value,
                lease_token=None,
                lease_expires_at=None,
                updated_at=_database_time(),
            )
            .returning(SourceTaskModel)
        )
        if row is None:
            self._session.expire_all()
            row = self._session.get(SourceTaskModel, str(task_id))
        if row is None:
            raise LookupError(f"source task does not exist: {task_id}")
        return SourceTaskSnapshot.model_validate(
            {
                "id": task_id,
                "operation": SourceOperation(row.operation),
                "status": SourceTaskStatus(row.status),
                "request_deadline_at": _aware(row.request_deadline_at),
                "result": row.result_payload
                if row.status == SourceTaskStatus.COMPLETED.value
                else None,
                "failure": row.result_payload
                if row.status == SourceTaskStatus.FAILED.value
                else None,
            }
        )

    def get_snapshot(self, task_id: UUID) -> SourceTaskSnapshot:
        self._abandon_expired(task_id=task_id)
        self._session.expire_all()
        row = self._session.get(SourceTaskModel, str(task_id))
        if row is None:
            raise LookupError(f"source task does not exist: {task_id}")
        return SourceTaskSnapshot.model_validate(
            {
                "id": task_id,
                "operation": SourceOperation(row.operation),
                "status": SourceTaskStatus(row.status),
                "request_deadline_at": _aware(row.request_deadline_at),
                "result": row.result_payload
                if row.status == SourceTaskStatus.COMPLETED.value
                else None,
                "failure": row.result_payload
                if row.status == SourceTaskStatus.FAILED.value
                else None,
            }
        )

    def _abandon_expired(
        self,
        *,
        source_id: str | None = None,
        task_id: UUID | None = None,
    ) -> None:
        expired = update(SourceTaskModel).where(
            SourceTaskModel.status.in_(_ACTIONABLE_STATUSES),
            _database_time_is_at_or_after(SourceTaskModel.request_deadline_at),
        )
        if source_id is not None:
            expired = expired.where(SourceTaskModel.source_id == source_id)
        if task_id is not None:
            expired = expired.where(SourceTaskModel.id == str(task_id))
        self._session.execute(
            expired.values(
                status=SourceTaskStatus.ABANDONED.value,
                lease_token=None,
                lease_expires_at=None,
                updated_at=_database_time(),
            )
        )


class QueuedBrowserTransport:
    """Await browser execution to a durable deadline, then compensate within a local bound."""

    def __init__(
        self,
        service: SourceTaskService,
        source_id: SourceId,
        *,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 0.1,
        cleanup_timeout_seconds: float | None = None,
    ) -> None:
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("browser transport timing values must be positive")
        self._service = service
        self._source_id = source_id
        self._timeout = timeout_seconds
        self._poll_interval = poll_interval_seconds
        persistence_timeout = service.persistence_timeout_seconds
        self._cleanup_timeout = (
            persistence_timeout + _CLEANUP_SCHEDULING_GRACE_SECONDS
            if cleanup_timeout_seconds is None
            else cleanup_timeout_seconds
        )
        if self._cleanup_timeout <= 0:
            raise ValueError("browser transport cleanup timeout must be positive")

    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, object]]:
        typed_operation = SourceOperation(operation)
        payload = _browser_request_payload(typed_operation, query=query, limit=limit)
        task_id = uuid4()
        request = SourceTaskRequest.model_validate(
            {"source_id": self._source_id, "payload": payload}
        )
        request_deadline_at = datetime.now(UTC) + timedelta(seconds=self._timeout)
        enqueue_task = asyncio.create_task(
            asyncio.to_thread(
                self._service.enqueue,
                request,
                task_id=task_id,
                request_deadline_at=request_deadline_at,
            )
        )
        try:
            async with asyncio.timeout(self._timeout):
                await asyncio.shield(enqueue_task)
                while True:
                    snapshot = await asyncio.to_thread(self._service.snapshot, task_id)
                    if snapshot.status is SourceTaskStatus.COMPLETED:
                        result = snapshot.result
                        if result is None:
                            raise TypeError("browser source result envelope is missing")
                        return [dict(item) for item in result.items]
                    if snapshot.status is SourceTaskStatus.CANCELLED:
                        raise CancelledSourceTaskError("browser source task was cancelled")
                    if snapshot.status is SourceTaskStatus.ABANDONED:
                        raise TimeoutError("browser source task request deadline expired")
                    if snapshot.status is SourceTaskStatus.FAILED:
                        failure = snapshot.failure
                        if failure is None:
                            raise RuntimeError("browser source task failed without classification")
                        raise RuntimeError(
                            f"browser source task failed: {failure.code} ({failure.error_type})"
                        )
                    await asyncio.sleep(self._poll_interval)
        except BaseException as original_error:
            cleanup_task = asyncio.create_task(self._compensate(enqueue_task, task_id))
            await self._await_cleanup_resistant(cleanup_task)
            raise original_error

    async def _compensate(self, enqueue_task: asyncio.Task[UUID], task_id: UUID) -> None:
        """Try to cancel a persisted row within the finite local persistence bound."""

        try:
            async with asyncio.timeout(self._cleanup_timeout):
                try:
                    await asyncio.shield(enqueue_task)
                except Exception:
                    return
                await asyncio.to_thread(self._service.cancel, task_id)
        except TimeoutError:
            enqueue_task.add_done_callback(_consume_late_enqueue_outcome)
            logger.warning("source task cleanup reached its persistence deadline")
        except Exception as error:
            logger.warning("source task cleanup failed (%s)", type(error).__name__)

    async def _await_cleanup_resistant(self, cleanup_task: asyncio.Task[None]) -> None:
        """Ignore repeated parent cancellation until cleanup finishes or reaches its own bound."""

        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
            except BaseException as error:
                logger.warning("source task cleanup failed (%s)", type(error).__name__)
                break


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _browser_request_payload(
    operation: SourceOperation, *, query: str | None, limit: int
) -> dict[str, object]:
    payload: dict[str, object] = {"operation": operation.value, "limit": limit}
    if operation is SourceOperation.SEARCH:
        payload["query"] = query
    elif operation is SourceOperation.RELATED:
        payload["seed"] = query
    elif operation is SourceOperation.CREATOR:
        payload["creator"] = query
    elif operation is SourceOperation.COMMUNITY:
        payload["community"] = query
    return payload


def _database_time() -> Any:
    """SQLite UTC clock with fractional-second precision, expressed through SQLAlchemy."""

    return func.strftime("%Y-%m-%d %H:%M:%f", "now")


def _database_time_plus(*, seconds: int) -> Any:
    return func.strftime("%Y-%m-%d %H:%M:%f", "now", f"+{seconds} seconds")


def _database_time_is_before(column: Any) -> Any:
    return func.julianday(column) > func.julianday("now")


def _database_time_is_at_or_after(column: Any) -> Any:
    return func.julianday(column) <= func.julianday("now")


def _consume_late_enqueue_outcome(task: asyncio.Task[UUID]) -> None:
    """Retrieve a late enqueue result so its exception never reaches the loop handler."""

    try:
        task.result()
    except asyncio.CancelledError:
        return
    except BaseException as error:
        logger.warning("late source task enqueue failed (%s)", type(error).__name__)
