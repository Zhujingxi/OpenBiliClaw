"""Agent-owned policy journal: append-only, strictly separate from the user-evidence plane.

The policy journal records what the *system* decided and learned — briefs, hypotheses,
lessons, outcomes. It never stores user evidence and never foreign-keys into the
understanding tables; cross-plane references are opaque ID strings.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import AwareDatetime, ConfigDict, Field, JsonValue

from openbiliclaw.core._pydantic import StrictBaseModel

from .models import record_identity

if TYPE_CHECKING:
    from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase

__all__ = [
    "JournalBrief",
    "JournalHypothesis",
    "JournalLesson",
    "JournalOutcome",
    "PolicyJournal",
    "SqlitePolicyJournal",
    "record_identity",
]


class _JournalRecord(StrictBaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    created_at: AwareDatetime


class JournalBrief(_JournalRecord):
    """One compiled strategy episode: the typed proposal and its compiled result."""

    brief_id: str = Field(pattern=r"^brief_[0-9a-f]{32}$")
    episode_id: str = Field(min_length=1, max_length=200)
    status: Literal["shadow", "active", "superseded"]
    payload: dict[str, JsonValue]


class JournalHypothesis(_JournalRecord):
    """An exploration hypothesis. Immutable; evolution is recorded as outcomes."""

    hypothesis_id: str = Field(pattern=r"^hyp_[0-9a-f]{32}$")
    arm: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    statement: str = Field(min_length=1, max_length=1000)
    evidence_refs: tuple[str, ...]  # opaque IDs into the user-evidence plane
    falsification: str = Field(min_length=1, max_length=500)
    expires_at: AwareDatetime


class JournalLesson(_JournalRecord):
    """A distilled, decaying policy lesson with traceable source references."""

    lesson_id: str = Field(pattern=r"^lesson_[0-9a-f]{32}$")
    statement: str = Field(min_length=1, max_length=1000)
    source_refs: tuple[str, ...]


class JournalOutcome(_JournalRecord):
    """One attempt/result event against a hypothesis; feeds statistical allocation."""

    outcome_id: str = Field(pattern=r"^outcome_[0-9a-f]{32}$")
    hypothesis_id: str = Field(min_length=1, max_length=200)
    kind: Literal["attempt", "success", "failure", "killed"]
    detail: str = Field(default="", max_length=1000)


class PolicyJournal(Protocol):
    """Append-only persistence port for the agent-owned policy plane."""

    async def append_brief(self, record: JournalBrief) -> None: ...
    async def load_brief(self, brief_id: str) -> JournalBrief: ...
    async def append_hypothesis(self, record: JournalHypothesis) -> None: ...
    async def load_hypothesis(self, hypothesis_id: str) -> JournalHypothesis: ...
    async def append_lesson(self, record: JournalLesson) -> None: ...
    async def list_lessons(self) -> tuple[JournalLesson, ...]: ...
    async def append_outcome(self, record: JournalOutcome) -> None: ...
    async def list_outcomes(self, hypothesis_id: str) -> tuple[JournalOutcome, ...]: ...


_TABLE_KEYS = {
    "policy_briefs": "brief_id",
    "policy_hypotheses": "hypothesis_id",
    "policy_lessons": "lesson_id",
    "policy_outcomes": "outcome_id",
}


class SqlitePolicyJournal:
    """SQLite journal over the append-only `policy_*` tables (schema V7)."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    async def _append(self, table: str, record: _JournalRecord, **columns: str) -> None:
        names = (_TABLE_KEYS[table], *columns, "record_json", "created_at")
        values = (
            getattr(record, _TABLE_KEYS[table]),
            *columns.values(),
            json.dumps(record.model_dump(mode="json")),
            record.created_at.isoformat(),
        )
        placeholders = ", ".join("?" for _ in values)
        async with self._database.transaction() as session:
            await session.execute(
                f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})", values
            )

    async def append_brief(self, record: JournalBrief) -> None:
        await self._append("policy_briefs", record, episode_id=record.episode_id)

    async def load_brief(self, brief_id: str) -> JournalBrief:
        async with self._database.transaction() as session:
            row = await session.fetch_one(
                "SELECT record_json FROM policy_briefs WHERE brief_id = ?", (brief_id,)
            )
        if row is None:
            raise KeyError(brief_id)
        return JournalBrief.model_validate(json.loads(cast("str", row[0])))

    async def append_hypothesis(self, record: JournalHypothesis) -> None:
        await self._append("policy_hypotheses", record, arm=record.arm)

    async def load_hypothesis(self, hypothesis_id: str) -> JournalHypothesis:
        async with self._database.transaction() as session:
            row = await session.fetch_one(
                "SELECT record_json FROM policy_hypotheses WHERE hypothesis_id = ?",
                (hypothesis_id,),
            )
        if row is None:
            raise KeyError(hypothesis_id)
        return JournalHypothesis.model_validate(json.loads(cast("str", row[0])))

    async def append_lesson(self, record: JournalLesson) -> None:
        await self._append("policy_lessons", record)

    async def list_lessons(self) -> tuple[JournalLesson, ...]:
        async with self._database.transaction() as session:
            rows = await session.fetch_all(
                "SELECT record_json FROM policy_lessons ORDER BY created_at"
            )
        return tuple(JournalLesson.model_validate(json.loads(cast("str", row[0]))) for row in rows)

    async def append_outcome(self, record: JournalOutcome) -> None:
        await self._append("policy_outcomes", record, hypothesis_id=record.hypothesis_id)

    async def list_outcomes(self, hypothesis_id: str) -> tuple[JournalOutcome, ...]:
        async with self._database.transaction() as session:
            rows = await session.fetch_all(
                "SELECT record_json FROM policy_outcomes"
                " WHERE hypothesis_id = ? ORDER BY created_at",
                (hypothesis_id,),
            )
        return tuple(JournalOutcome.model_validate(json.loads(cast("str", row[0]))) for row in rows)
