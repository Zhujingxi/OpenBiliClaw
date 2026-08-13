"""Domain persistence contract and concrete SQLite observation repository."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel

from .models import Observation, observation_adapter

if TYPE_CHECKING:
    from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase, SqliteSession


class InsertStatus(StrEnum):
    INSERTED = "inserted"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class RepositoryInsertResult:
    observation_id: str
    status: InsertStatus


class ObservationPage(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[Observation, ...]
    next_cursor: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")


class ObservationRepository(Protocol):
    async def insert_batch(
        self, observations: tuple[Observation, ...]
    ) -> tuple[RepositoryInsertResult, ...]: ...
    async def read(self, *, after_cursor: str | None, limit: int) -> ObservationPage: ...


class SqliteObservationRepository:
    """Immutable typed observations over the target infrastructure schema."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    async def insert_batch(
        self, observations: tuple[Observation, ...]
    ) -> tuple[RepositoryInsertResult, ...]:
        async with self._database.transaction() as session:
            return await self.insert_batch_session(session, observations)

    async def insert_batch_session(
        self, session: SqliteSession, observations: tuple[Observation, ...]
    ) -> tuple[RepositoryInsertResult, ...]:
        """Insert a typed batch inside the caller's existing database transaction."""

        results: list[RepositoryInsertResult] = []
        for event in observations:
            existing = await session.fetch_one(
                "SELECT observation_id FROM observations WHERE producer=? AND idempotency_key=?",
                (event.provenance.producer_id, event.idempotency_key),
            )
            if existing is not None:
                results.append(RepositoryInsertResult(str(existing[0]), InsertStatus.DUPLICATE))
                continue
            await self._ensure_content(session, event)
            content_id = await self._content_id(session, event)
            document = observation_adapter.dump_python(event, mode="json")
            await session.execute(
                "INSERT INTO observations("
                "observation_id,content_id,kind,occurred_at,strength,producer,"
                "idempotency_key,payload_json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    event.observation_id,
                    content_id,
                    event.event_type,
                    event.occurred_at.isoformat(),
                    _strength(event),
                    event.provenance.producer_id,
                    event.idempotency_key,
                    json.dumps(document, separators=(",", ":"), sort_keys=True),
                ),
            )
            results.append(RepositoryInsertResult(event.observation_id, InsertStatus.INSERTED))
        return tuple(results)

    async def read(self, *, after_cursor: str | None, limit: int) -> ObservationPage:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        cursor = int(after_cursor) if after_cursor is not None else 0
        async with self._database.transaction() as session:
            rows = await session.fetch_all(
                "SELECT rowid,payload_json FROM observations WHERE rowid>? ORDER BY rowid LIMIT ?",
                (cursor, limit),
            )
        items = tuple(observation_adapter.validate_json(str(row[1])) for row in rows)
        next_cursor = str(rows[-1][0]) if rows else after_cursor
        return ObservationPage(items=items, next_cursor=next_cursor)

    @staticmethod
    async def _ensure_content(session: SqliteSession, event: Observation) -> None:
        ref = event.content_ref
        if ref is None:
            return
        await session.execute(
            "INSERT OR IGNORE INTO content_references("
            "provider,external_id,kind,canonical_url) VALUES(?,?,?,?)",
            (
                ref.provider_id.value,
                ref.provider_content_id,
                ref.content_kind.value,
                ref.canonical_url,
            ),
        )

    @staticmethod
    async def _content_id(session: SqliteSession, event: Observation) -> int | None:
        ref = event.content_ref
        if ref is None:
            return None
        row = await session.fetch_one(
            "SELECT content_id FROM content_references WHERE provider=? AND external_id=?",
            (ref.provider_id.value, ref.provider_content_id),
        )
        if row is None or not isinstance(row[0], int):
            raise RuntimeError("content reference insertion failed")
        return row[0]


def _strength(event: Observation) -> float:
    return {"low": 0.25, "medium": 0.6, "high": 1.0}[event.provenance.trust_level.value]
