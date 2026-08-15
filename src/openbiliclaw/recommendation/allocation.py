"""Pure intent-conditioned Thompson allocation for recommendation episodes.

``exploit`` is a first-class strategy arm: callers may supply one or more rows with
that arm name from exploit-slot outcomes. With no such evidence it has the same
uniform Beta(1, 1) prior as every exploration hypothesis. Consequently cold start
explores in proportion to the number of active exploration hypotheses; exploration
shrinks only when learned exploit evidence wins, not because of a fixed percentage.

An exploration hypothesis with no resolved outcomes borrows successes and failures
from its strategy family. Once it has a resolution, its own evidence defines its
posterior. Attempt counts are accepted for the registry contract and deliberately do
not enter Beta parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from random import Random

Intent: TypeAlias = Literal["enjoy", "accomplish", "deepen", "explore", "uncertain"]
HypothesisCounts: TypeAlias = tuple[str, str, int, int, int]

__all__ = [
    "AllocationDecision",
    "AllocationSample",
    "HypothesisCounts",
    "Intent",
    "ThompsonAllocator",
]


def _posterior_parameters(
    *,
    successes: int,
    failures: int,
    family_successes: int = 0,
    family_failures: int = 0,
) -> tuple[int, int]:
    """Build Beta parameters, borrowing family resolutions only before the first own one."""

    if successes == 0 and failures == 0:
        successes, failures = family_successes, family_failures
    return 1 + successes, 1 + failures


@dataclass(frozen=True, slots=True)
class AllocationSample:
    """One posterior draw retained for audit and deterministic replay."""

    arm: str
    hypothesis_id: str | None
    alpha: int
    beta: int
    value: float


@dataclass(frozen=True, slots=True)
class AllocationDecision:
    """Episode-level explore/exploit choice and its posterior draws."""

    intent: Intent
    explore: bool
    arm: str | None
    hypothesis_id: str | None
    samples: tuple[AllocationSample, ...]


class ThompsonAllocator:
    """Draw Beta posteriors using caller-owned randomness for replayability."""

    def __init__(self, random: Random) -> None:
        self._random = random

    def decide(
        self,
        *,
        intent: Intent,
        hypotheses: tuple[HypothesisCounts, ...],
    ) -> AllocationDecision:
        """Allocate one episode according to intent and learned posterior evidence."""

        rows = tuple(sorted(hypotheses, key=lambda row: (row[1], row[0])))
        exploration = tuple(row for row in rows if row[1] != "exploit")
        if intent in ("deepen", "explore") and not exploration:
            raise ValueError("no exploration hypotheses available")

        family_outcomes = {
            arm: (
                sum(row[3] for row in exploration if row[1] == arm),
                sum(row[4] for row in exploration if row[1] == arm),
            )
            for arm in {row[1] for row in exploration}
        }
        exploit_successes = sum(row[3] for row in rows if row[1] == "exploit")
        exploit_failures = sum(row[4] for row in rows if row[1] == "exploit")
        samples = [
            self._sample(
                arm="exploit",
                hypothesis_id=None,
                successes=exploit_successes,
                failures=exploit_failures,
            )
        ]
        for hypothesis_id, arm, _attempts, successes, failures in exploration:
            family_successes, family_failures = family_outcomes[arm]
            samples.append(
                self._sample(
                    arm=arm,
                    hypothesis_id=hypothesis_id,
                    successes=successes,
                    failures=failures,
                    family_successes=family_successes,
                    family_failures=family_failures,
                )
            )
        audit = tuple(samples)

        if intent in ("enjoy", "accomplish"):
            return AllocationDecision(intent, False, None, None, audit)
        if intent in ("deepen", "explore"):
            winner = max(samples[1:], key=lambda sample: sample.value)
            return AllocationDecision(intent, True, winner.arm, winner.hypothesis_id, audit)

        winner = max(samples, key=lambda sample: sample.value)
        if winner.arm == "exploit":
            return AllocationDecision(intent, False, None, None, audit)
        return AllocationDecision(intent, True, winner.arm, winner.hypothesis_id, audit)

    def _sample(
        self,
        *,
        arm: str,
        hypothesis_id: str | None,
        successes: int,
        failures: int,
        family_successes: int = 0,
        family_failures: int = 0,
    ) -> AllocationSample:
        alpha, beta = _posterior_parameters(
            successes=successes,
            failures=failures,
            family_successes=family_successes,
            family_failures=family_failures,
        )
        return AllocationSample(
            arm=arm,
            hypothesis_id=hypothesis_id,
            alpha=alpha,
            beta=beta,
            value=self._random.betavariate(alpha, beta),
        )
