"""Typed exploration hypotheses stored only in the agent policy journal.

Outcome write contract (what B3's sampler relies on): each scored exposure is exactly
one ``attempt`` event followed by at most one resolution event (``success`` or
``failure``). Posteriors count resolutions, not attempts: Beta(1 + successes,
1 + failures). ``attempt`` rows exist for funnel metrics and never enter the Beta.
Terminal rule: once a ``killed`` event exists the hypothesis is closed — further
non-kill outcomes are rejected; ``kill`` on a killed hypothesis is idempotent.
Expired-but-unkilled hypotheses still accept outcomes (late engagement is valid data).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from openbiliclaw.recommendation.policy_journal import (
    JournalHypothesis,
    JournalOutcome,
    OutcomeKind,
    PolicyJournal,
)

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["HypothesisRegistry", "OutcomeKind"]


class HypothesisRegistry:
    """Register hypotheses and derive their state from append-only journal events."""

    def __init__(
        self,
        journal: PolicyJournal,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._journal = journal
        self._clock = clock

    async def register(
        self,
        *,
        arm: str,
        statement: str,
        evidence_refs: tuple[str, ...],
        falsification: str,
        expires_at: datetime,
    ) -> JournalHypothesis:
        """Validate and append an immutable exploration hypothesis."""

        record = JournalHypothesis(
            hypothesis_id=f"hyp_{uuid4().hex}",
            arm=arm,
            statement=statement,
            evidence_refs=evidence_refs,
            falsification=falsification,
            expires_at=expires_at,
            created_at=self._clock(),
        )
        if record.expires_at <= record.created_at:
            raise ValueError("hypothesis expiry must be in the future")
        await self._journal.append_hypothesis(record)
        return record

    async def active(self, now: datetime | None = None) -> tuple[JournalHypothesis, ...]:
        """Return unexpired hypotheses that have no explicit kill event."""

        at = self._clock() if now is None else now
        active: list[JournalHypothesis] = []
        for hypothesis in await self._journal.list_hypotheses():
            if hypothesis.expires_at <= at:
                continue
            outcomes = await self._journal.list_outcomes(hypothesis.hypothesis_id)
            if not any(outcome.kind == "killed" for outcome in outcomes):
                active.append(hypothesis)
        return tuple(active)

    async def record_outcome(
        self, hypothesis_id: str, kind: OutcomeKind, detail: str
    ) -> JournalOutcome:
        """Append an outcome; killed hypotheses are closed to further events."""

        await self._journal.load_hypothesis(hypothesis_id)
        outcomes = await self._journal.list_outcomes(hypothesis_id)
        killed = next((outcome for outcome in outcomes if outcome.kind == "killed"), None)
        if killed is not None:
            if kind == "killed":
                return killed  # idempotent: kill on a killed hypothesis returns the event
            raise ValueError(f"hypothesis {hypothesis_id} is killed")
        outcome = JournalOutcome(
            outcome_id=f"outcome_{uuid4().hex}",
            hypothesis_id=hypothesis_id,
            kind=kind,
            detail=detail,
            created_at=self._clock(),
        )
        await self._journal.append_outcome(outcome)
        return outcome

    async def posterior(self, hypothesis_id: str) -> tuple[int, int, int]:
        """Return (attempts, successes, failures); the Beta uses resolutions only."""

        await self._journal.load_hypothesis(hypothesis_id)
        outcomes = await self._journal.list_outcomes(hypothesis_id)
        attempts = sum(outcome.kind == "attempt" for outcome in outcomes)
        successes = sum(outcome.kind == "success" for outcome in outcomes)
        failures = sum(outcome.kind == "failure" for outcome in outcomes)
        return attempts, successes, failures

    async def kill(self, hypothesis_id: str, reason: str) -> JournalOutcome:
        """Deactivate a hypothesis through an append-only kill event."""

        return await self.record_outcome(hypothesis_id, "killed", reason)
