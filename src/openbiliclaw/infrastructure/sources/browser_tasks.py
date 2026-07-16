"""SQLAlchemy persistence adapter for generic logged-in browser source tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update

from openbiliclaw.features.sources.domain import (
    ClaimedSourceTask,
    SourceCapability,
    SourceId,
    SourceTaskCompletion,
    SourceTaskRequest,
)
from openbiliclaw.features.sources.service import (
    SourceTaskCompletionConflictError,
    StaleSourceTaskLeaseError,
)
from openbiliclaw.infrastructure.database.models import SourceTaskModel

if TYPE_CHECKING:
    from pydantic import JsonValue
    from sqlalchemy.orm import Session


class SQLAlchemyBrowserTaskRepository:
    """Atomically claim expired/pending tasks and preserve idempotent completions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_pending(self, request: SourceTaskRequest, *, now: datetime) -> UUID:
        task_id = uuid4()
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
                    operation=SourceCapability(row.operation),
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


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
