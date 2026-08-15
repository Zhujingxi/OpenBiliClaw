"""Hypothesis registry over the agent-owned policy journal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.recommendation.hypotheses import HypothesisRegistry
from openbiliclaw.recommendation.policy_journal import (
    JournalBrief,
    JournalHypothesis,
    JournalLesson,
    JournalOutcome,
    SqlitePolicyJournal,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 8, 15, tzinfo=UTC)


class MemoryPolicyJournal:
    """PolicyJournal test double; notably has no user-evidence dependency."""

    def __init__(self) -> None:
        self.hypotheses: dict[str, JournalHypothesis] = {}
        self.outcomes: list[JournalOutcome] = []

    async def append_brief(self, record: JournalBrief) -> None:
        raise AssertionError("registry must not append briefs")

    async def load_brief(self, brief_id: str) -> JournalBrief:
        raise AssertionError("registry must not load briefs")

    async def list_briefs(self, *, limit: int) -> tuple[JournalBrief, ...]:
        raise AssertionError("registry must not list briefs")

    async def append_hypothesis(self, record: JournalHypothesis) -> None:
        self.hypotheses[record.hypothesis_id] = record

    async def load_hypothesis(self, hypothesis_id: str) -> JournalHypothesis:
        try:
            return self.hypotheses[hypothesis_id]
        except KeyError:
            raise KeyError(hypothesis_id) from None

    async def list_hypotheses(self) -> tuple[JournalHypothesis, ...]:
        return tuple(self.hypotheses.values())

    async def append_lesson(self, record: JournalLesson) -> None:
        raise AssertionError("registry must not append lessons")

    async def list_lessons(self) -> tuple[JournalLesson, ...]:
        raise AssertionError("registry must not list lessons")

    async def append_outcome(self, record: JournalOutcome) -> None:
        self.outcomes.append(record)

    async def list_outcomes(self, hypothesis_id: str) -> tuple[JournalOutcome, ...]:
        return tuple(row for row in self.outcomes if row.hypothesis_id == hypothesis_id)


async def _register(
    registry: HypothesisRegistry,
    *,
    arm: str = "weak-signal",
    statement: str = "A weak signal may still be relevant",
    expires_at: datetime = NOW + timedelta(days=7),
) -> JournalHypothesis:
    return await registry.register(
        arm=arm,
        statement=statement,
        evidence_refs=("obs_opaque",),
        falsification="two failed attempts",
        expires_at=expires_at,
    )


async def test_register_load_round_trip_with_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "registry.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    try:
        journal = SqlitePolicyJournal(database)
        registered = await _register(HypothesisRegistry(journal, clock=lambda: NOW))

        assert await journal.load_hypothesis(registered.hypothesis_id) == registered
        assert await journal.list_hypotheses() == (registered,)
    finally:
        await database.close()


async def test_ensure_active_reuses_one_standing_hypothesis_per_arm() -> None:
    journal = MemoryPolicyJournal()
    registry = HypothesisRegistry(journal, clock=lambda: NOW)
    first = await registry.ensure_active(
        arm="source-novel",
        statement="Public feeds may expose useful novelty",
        evidence_refs=("system:replenishment",),
        falsification="resolved failures exceed successes",
        expires_at=NOW + timedelta(days=7),
        now=NOW,
    )
    second = await registry.ensure_active(
        arm="source-novel",
        statement="Public feeds may expose useful novelty",
        evidence_refs=("system:replenishment",),
        falsification="resolved failures exceed successes",
        expires_at=NOW + timedelta(days=7),
        now=NOW,
    )

    assert second == first
    assert tuple(journal.hypotheses.values()) == (first,)


async def test_active_filters_expired_and_killed_hypotheses() -> None:
    journal = MemoryPolicyJournal()
    registry = HypothesisRegistry(journal, clock=lambda: NOW)
    active = await _register(registry, statement="active")
    await _register(
        registry,
        statement="expired",
        expires_at=NOW + timedelta(hours=1),
    )
    killed = await _register(registry, arm="source-novel", statement="killed")
    await registry.kill(killed.hypothesis_id, "pre-registered condition met")

    assert await registry.active(NOW + timedelta(hours=2)) == (active,)


async def test_posterior_counts_successes_and_attempts_from_outcome_stream() -> None:
    journal = MemoryPolicyJournal()
    registry = HypothesisRegistry(journal, clock=lambda: NOW)
    hypothesis = await _register(registry)
    await registry.record_outcome(hypothesis.hypothesis_id, "attempt", "shown once")
    await registry.record_outcome(hypothesis.hypothesis_id, "success", "meaningful engagement")
    await registry.record_outcome(hypothesis.hypothesis_id, "attempt", "shown twice")
    await registry.record_outcome(hypothesis.hypothesis_id, "failure", "dismissed")

    assert await registry.posterior(hypothesis.hypothesis_id) == (2, 1, 1)


async def test_killed_hypothesis_rejects_further_outcomes_and_double_kill_is_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "killed.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    try:
        journal = SqlitePolicyJournal(database)
        registry = HypothesisRegistry(journal, clock=lambda: NOW)
        hypothesis = await _register(registry)
        kill = await registry.kill(hypothesis.hypothesis_id, "falsified")

        again = await registry.kill(hypothesis.hypothesis_id, "duplicate kill")
        assert again == kill  # idempotent: no second kill event appended
        assert [
            outcome.kind for outcome in await journal.list_outcomes(hypothesis.hypothesis_id)
        ] == ["killed"]
        with pytest.raises(ValueError, match="killed"):
            await registry.record_outcome(hypothesis.hypothesis_id, "attempt", "too late")
        assert await registry.active() == ()
    finally:
        await database.close()


async def test_expired_but_unkilled_hypothesis_still_accepts_late_outcomes() -> None:
    registry = HypothesisRegistry(MemoryPolicyJournal(), clock=lambda: NOW)
    hypothesis = await _register(registry, expires_at=NOW + timedelta(hours=1))
    await registry.record_outcome(hypothesis.hypothesis_id, "attempt", "shown before expiry")
    await registry.record_outcome(hypothesis.hypothesis_id, "success", "engaged late")

    assert await registry.posterior(hypothesis.hypothesis_id) == (1, 1, 0)
    assert await registry.active(NOW + timedelta(hours=2)) == ()


async def test_record_outcome_rejects_unknown_hypothesis() -> None:
    registry = HypothesisRegistry(MemoryPolicyJournal(), clock=lambda: NOW)

    with pytest.raises(KeyError, match="hyp_missing"):
        await registry.record_outcome("hyp_missing", "attempt", "not persisted")


@pytest.mark.parametrize("arm", ["Weak-signal", "weak_signal", "-novel", "", "a" * 65])
async def test_register_validates_open_arm_format(arm: str) -> None:
    registry = HypothesisRegistry(MemoryPolicyJournal(), clock=lambda: NOW)

    with pytest.raises(ValidationError):
        await _register(registry, arm=arm)


async def test_register_requires_future_expiry() -> None:
    registry = HypothesisRegistry(MemoryPolicyJournal(), clock=lambda: NOW)

    with pytest.raises(ValueError, match="expiry must be in the future"):
        await _register(registry, expires_at=NOW)


@pytest.mark.parametrize(
    ("statement", "falsification"),
    [("", "two failed attempts"), ("A testable statement", "")],
)
async def test_register_requires_statement_and_falsification(
    statement: str, falsification: str
) -> None:
    registry = HypothesisRegistry(MemoryPolicyJournal(), clock=lambda: NOW)

    with pytest.raises(ValidationError):
        await registry.register(
            arm="dormant-interest",
            statement=statement,
            evidence_refs=("obs_opaque",),
            falsification=falsification,
            expires_at=NOW + timedelta(days=7),
        )
