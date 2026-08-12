"""Durable application-workflow idempotency journal."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase


class SqliteIdempotencyJournal:
    """Store workflow results across process restarts."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    async def get(self, key: str) -> str | None:
        async with self._database.transaction() as session:
            row = await session.fetch_one(
                "SELECT result_json FROM workflow_idempotency WHERE idempotency_key=?", (key,)
            )
        return str(row[0]) if row is not None else None

    async def put(self, key: str, value: str) -> None:
        async with self._database.transaction() as session:
            await session.execute(
                "INSERT INTO workflow_idempotency(idempotency_key,result_json,created_at) "
                "VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(idempotency_key) DO NOTHING",
                (key, value),
            )
