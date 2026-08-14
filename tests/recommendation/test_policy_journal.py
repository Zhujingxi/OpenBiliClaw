"""Policy journal: agent-owned plane, append-only, decoupled from user evidence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.recommendation.policy_journal import (
    JournalBrief,
    JournalHypothesis,
    JournalLesson,
    JournalOutcome,
    SqlitePolicyJournal,
    record_identity,
)

NOW = datetime(2026, 8, 15, tzinfo=UTC)


async def _journal(tmp_path: Path) -> tuple[SqliteDatabase, SqlitePolicyJournal]:
    path = tmp_path / "journal.db"
    await SchemaMigrator(path).migrate()
    db = SqliteDatabase(path)
    await db.open()
    return db, SqlitePolicyJournal(db)


@pytest.mark.asyncio
async def test_migration_creates_policy_journal_tables(tmp_path: Path) -> None:
    path = tmp_path / "schema.db"
    await SchemaMigrator(path).migrate()
    connection = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        connection.close()
    assert {
        "policy_briefs",
        "policy_hypotheses",
        "policy_lessons",
        "policy_outcomes",
    } <= tables


@pytest.mark.asyncio
async def test_journal_plane_has_no_user_evidence_coupling(tmp_path: Path) -> None:
    """Two-plane rule: policy tables cross-reference user evidence by opaque ID only."""

    path = tmp_path / "planes.db"
    await SchemaMigrator(path).migrate()
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name LIKE 'policy_%'"
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 4
    for name, sql in rows:
        assert "REFERENCES" not in sql, name
        assert "understanding_" not in sql, name


@pytest.mark.asyncio
async def test_brief_hypothesis_lesson_outcome_round_trip(tmp_path: Path) -> None:
    db, journal = await _journal(tmp_path)
    try:
        brief = JournalBrief(
            brief_id=record_identity("brief", "episode-1"),
            episode_id="episode-1",
            status="shadow",
            payload={"intent": "explore", "hypotheses": ["h1"]},
            created_at=NOW,
        )
        hypothesis = JournalHypothesis(
            hypothesis_id=record_identity("hyp", "dormant-cooking"),
            arm="dormant-interest",
            statement="user still responds to long-form cooking",
            evidence_refs=("ev_abc123",),  # opaque IDs into the user-evidence plane
            falsification="3 shown, 0 engaged",
            expires_at=NOW,
            created_at=NOW,
        )
        lesson = JournalLesson(
            lesson_id=record_identity("lesson", "l1"),
            statement="bridge arms underperform on weekday mornings",
            source_refs=(hypothesis.hypothesis_id,),
            created_at=NOW,
        )
        outcome = JournalOutcome(
            outcome_id=record_identity("outcome", "o1"),
            hypothesis_id=hypothesis.hypothesis_id,
            kind="success",
            detail="engaged within one exposure",
            created_at=NOW,
        )
        await journal.append_brief(brief)
        await journal.append_hypothesis(hypothesis)
        await journal.append_lesson(lesson)
        await journal.append_outcome(outcome)

        assert await journal.load_brief(brief.brief_id) == brief
        assert await journal.load_hypothesis(hypothesis.hypothesis_id) == hypothesis
        assert await journal.list_lessons() == (lesson,)
        assert await journal.list_outcomes(hypothesis.hypothesis_id) == (outcome,)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_journal_is_append_only(tmp_path: Path) -> None:
    db, journal = await _journal(tmp_path)
    try:
        brief = JournalBrief(
            brief_id=record_identity("brief", "immutable"),
            episode_id="episode-x",
            status="shadow",
            payload={},
            created_at=NOW,
        )
        await journal.append_brief(brief)
        with pytest.raises(sqlite3.IntegrityError):
            await journal.append_brief(brief)  # duplicate primary key rejected
        async with db.transaction() as session:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                await session.execute(
                    "UPDATE policy_briefs SET record_json='{}' WHERE brief_id=?",
                    (brief.brief_id,),
                )
        async with db.transaction() as session:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                await session.execute(
                    "DELETE FROM policy_briefs WHERE brief_id=?", (brief.brief_id,)
                )
    finally:
        await db.close()
