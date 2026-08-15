"""Durable SQLite pending-action repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import ApplicationError, ApplicationErrorCode

if TYPE_CHECKING:
    from datetime import datetime

    from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase

    from .content_actions import PendingAction, PendingActionResult


class SqlitePendingActionRepository:
    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    async def put(self, action: PendingAction) -> PendingAction:
        existing = await self.get(action.pending_action_id)
        if existing is not None:
            if existing.idempotency_key != action.idempotency_key:
                raise ApplicationError(ApplicationErrorCode.CONFLICT, "pending identity conflict")
            return existing
        async with self._database.transaction() as session:
            await session.execute(
                "INSERT INTO pending_actions("
                "action_id,kind,state,payload_json,idempotency_key,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    action.pending_action_id,
                    action.action_id,
                    "pending",
                    action.model_dump_json(),
                    action.idempotency_key,
                    action.created_at.isoformat(),
                    action.created_at.isoformat(),
                ),
            )
        return action

    async def get(self, pending_action_id: str) -> PendingAction | None:
        from .content_actions import PendingAction

        async with self._database.transaction() as session:
            row = await session.fetch_one(
                "SELECT payload_json FROM pending_actions WHERE action_id=?",
                (pending_action_id,),
            )
        return PendingAction.model_validate_json(str(row[0])) if row is not None else None

    async def complete(self, action: PendingAction, result: PendingActionResult) -> PendingAction:
        current = await self.get(action.pending_action_id)
        if current is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "pending action not found")
        if current.result is not None:
            return current
        completed = current.model_copy(update={"decision": "approved", "result": result})
        async with self._database.transaction() as session:
            changed = await session.execute(
                "UPDATE pending_actions SET state='completed',payload_json=?,updated_at=? "
                "WHERE action_id=? AND state='pending'",
                (
                    completed.model_dump_json(),
                    result.completed_at.isoformat(),
                    action.pending_action_id,
                ),
            )
        if changed != 1:
            reread = await self.get(action.pending_action_id)
            if reread is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "pending action not found")
            return reread
        return completed

    async def reject(self, action: PendingAction, *, decided_at: datetime) -> PendingAction:
        current = await self.get(action.pending_action_id)
        if current is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "pending action not found")
        if current.decision != "pending":
            return current
        rejected = current.model_copy(update={"decision": "rejected"})
        async with self._database.transaction() as session:
            changed = await session.execute(
                "UPDATE pending_actions SET state='cancelled',payload_json=?,updated_at=? "
                "WHERE action_id=? AND state='pending'",
                (
                    rejected.model_dump_json(),
                    decided_at.isoformat(),
                    action.pending_action_id,
                ),
            )
        if changed != 1:
            reread = await self.get(action.pending_action_id)
            if reread is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "pending action not found")
            return reread
        return rejected
