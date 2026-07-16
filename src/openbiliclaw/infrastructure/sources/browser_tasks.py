"""Durable browser source-task persistence and awaiting transport."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update

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
    CancelledSourceTaskError,
    SourceTaskCompletionConflictError,
    StaleSourceTaskLeaseError,
)
from openbiliclaw.infrastructure.database.models import SourceTaskModel

if TYPE_CHECKING:
    from pydantic import JsonValue
    from sqlalchemy.orm import Session

    from openbiliclaw.features.sources.service import SourceTaskService


class SQLAlchemyBrowserTaskRepository:
    """Atomically claim expired/pending tasks and preserve idempotent completions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_pending(self, request: SourceTaskRequest, *, task_id: UUID, now: datetime) -> UUID:
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
        now: datetime,
        lease_expires_at: datetime,
    ) -> ClaimedSourceTask | None:
        if not allowed_operations:
            return None
        claimable = or_(
            SourceTaskModel.status == "pending",
            and_(
                SourceTaskModel.status == "in_progress",
                SourceTaskModel.lease_expires_at <= now,
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
                    lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
                .returning(SourceTaskModel)
            )
            if row is not None:
                return ClaimedSourceTask(
                    id=UUID(row.id),
                    source_id=SourceId(row.source_id),
                    operation=SourceOperation(row.operation),
                    payload=row.request_payload,
                    lease_token=lease_token,
                    lease_expires_at=lease_expires_at,
                )
            self._session.expire_all()
        return None

    def complete(
        self,
        *,
        task_id: UUID,
        lease_token: str,
        result: dict[str, JsonValue],
        now: datetime,
    ) -> SourceTaskCompletion:
        row = self._session.scalar(
            update(SourceTaskModel)
            .where(
                SourceTaskModel.id == str(task_id),
                SourceTaskModel.status == "in_progress",
                SourceTaskModel.lease_token == lease_token,
                SourceTaskModel.lease_expires_at > now,
            )
            .values(
                status="completed",
                result_payload=result,
                lease_expires_at=None,
                updated_at=now,
            )
            .returning(SourceTaskModel)
        )
        if row is not None:
            return SourceTaskCompletion(id=task_id, completed_at=now, idempotent=False)
        self._session.expire_all()
        row = self._session.get(SourceTaskModel, str(task_id))
        if row is None:
            raise LookupError(f"source task does not exist: {task_id}")
        if row.status == SourceTaskStatus.CANCELLED:
            raise CancelledSourceTaskError("source task was cancelled")
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

    def cancel(self, task_id: UUID, *, now: datetime) -> SourceTaskSnapshot:
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
                updated_at=now,
            )
            .returning(SourceTaskModel)
        )
        if row is None:
            self._session.expire_all()
            row = self._session.get(SourceTaskModel, str(task_id))
        if row is None:
            raise LookupError(f"source task does not exist: {task_id}")
        return SourceTaskSnapshot(
            id=task_id,
            status=SourceTaskStatus(row.status),
            result=row.result_payload,
        )

    def get_snapshot(self, task_id: UUID) -> SourceTaskSnapshot:
        row = self._session.get(SourceTaskModel, str(task_id))
        if row is None:
            raise LookupError(f"source task does not exist: {task_id}")
        return SourceTaskSnapshot(
            id=task_id,
            status=SourceTaskStatus(row.status),
            result=row.result_payload,
        )


class QueuedBrowserTransport:
    """Enqueue a typed operation and await its extension result with a hard bound."""

    def __init__(
        self,
        service: SourceTaskService,
        source_id: SourceId,
        *,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("browser transport timing values must be positive")
        self._service = service
        self._source_id = source_id
        self._timeout = timeout_seconds
        self._poll_interval = poll_interval_seconds

    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, object]]:
        typed_operation = SourceOperation(operation)
        payload: dict[str, JsonValue] = {"limit": limit}
        if query is not None:
            payload["query"] = query
        task_id = uuid4()
        request = SourceTaskRequest(
            source_id=self._source_id,
            operation=typed_operation,
            payload=payload,
        )
        enqueue_task = asyncio.create_task(
            asyncio.to_thread(self._service.enqueue, request, task_id=task_id)
        )
        try:
            async with asyncio.timeout(self._timeout):
                await asyncio.shield(enqueue_task)
                while True:
                    snapshot = await asyncio.to_thread(self._service.snapshot, task_id)
                    if snapshot.status is SourceTaskStatus.COMPLETED:
                        result = snapshot.result or {}
                        items = result.get("items")
                        if not isinstance(items, tuple):
                            raise TypeError("browser source result must contain an items array")
                        if not all(isinstance(item, Mapping) for item in items):
                            raise TypeError("browser source result items must be objects")
                        return [dict(item) for item in items]
                    if snapshot.status is SourceTaskStatus.CANCELLED:
                        raise CancelledSourceTaskError("browser source task was cancelled")
                    await asyncio.sleep(self._poll_interval)
        except BaseException:
            await asyncio.shield(self._compensate(enqueue_task, task_id))
            raise

    async def _compensate(self, enqueue_task: asyncio.Task[UUID], task_id: UUID) -> None:
        """Wait out an in-flight insert, then make its row terminal before returning."""

        try:
            await asyncio.shield(enqueue_task)
        except Exception:
            return
        await asyncio.to_thread(self._service.cancel, task_id)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
