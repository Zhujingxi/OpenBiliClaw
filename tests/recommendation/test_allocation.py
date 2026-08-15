"""Statistical allocation over active exploration hypotheses."""

from __future__ import annotations

from random import Random

import pytest

from openbiliclaw.recommendation.allocation import (
    HypothesisCounts,
    Intent,
    ThompsonAllocator,
)


def _cold_hypotheses() -> tuple[HypothesisCounts, ...]:
    return (
        ("hyp_weak", "weak-signal", 0, 0, 0),
        ("hyp_dormant", "dormant-interest", 0, 0, 0),
        ("hyp_source", "source-novel", 0, 0, 0),
    )


def test_fixed_seed_reproduces_decision_and_audit_samples() -> None:
    hypotheses = (
        ("exploit", "exploit", 12, 8, 4),
        ("hyp_weak", "weak-signal", 5, 3, 2),
        ("hyp_source", "source-novel", 4, 1, 3),
    )

    first = ThompsonAllocator(Random(17)).decide(intent="uncertain", hypotheses=hypotheses)
    replay = ThompsonAllocator(Random(17)).decide(intent="uncertain", hypotheses=hypotheses)
    reordered = ThompsonAllocator(Random(17)).decide(
        intent="uncertain", hypotheses=tuple(reversed(hypotheses))
    )

    assert replay == first == reordered
    assert first.samples
    exploit_sample = next(sample for sample in first.samples if sample.arm == "exploit")
    assert (exploit_sample.alpha, exploit_sample.beta) == (9, 5)
    assert all(0.0 <= sample.value <= 1.0 for sample in first.samples)
    assert all(sample.alpha >= 1 and sample.beta >= 1 for sample in first.samples)


def test_uniform_cold_start_elevates_exploration_without_a_policy_percentage() -> None:
    allocator = ThompsonAllocator(Random(41))

    explored = sum(
        allocator.decide(intent="uncertain", hypotheses=_cold_hypotheses()).explore
        for _ in range(1_000)
    )

    # Three uniform exploration posteriors compete with one uniform exploit baseline.
    assert 680 <= explored <= 820


def test_exploit_evidence_shifts_uncertain_allocation_toward_exploit() -> None:
    allocator = ThompsonAllocator(Random(73))
    hypotheses = (
        ("exploit", "exploit", 100, 90, 10),
        ("hyp_weak", "weak-signal", 20, 4, 16),
        ("hyp_source", "source-novel", 20, 3, 17),
    )

    explored = sum(
        allocator.decide(intent="uncertain", hypotheses=hypotheses).explore for _ in range(500)
    )

    assert explored < 10


@pytest.mark.parametrize("intent", ["enjoy", "accomplish"])
def test_exploit_intents_always_exploit(intent: Intent) -> None:
    allocator = ThompsonAllocator(Random(3))

    decisions = [allocator.decide(intent=intent, hypotheses=_cold_hypotheses()) for _ in range(50)]

    assert all(decision.explore is False for decision in decisions)
    assert all(decision.arm is None and decision.hypothesis_id is None for decision in decisions)


@pytest.mark.parametrize("intent", ["deepen", "explore"])
def test_exploration_intents_always_choose_an_exploration_hypothesis(intent: Intent) -> None:
    decision = ThompsonAllocator(Random(5)).decide(
        intent=intent,
        hypotheses=(
            ("exploit", "exploit", 100, 99, 1),
            *_cold_hypotheses(),
        ),
    )

    assert decision.explore is True
    assert decision.arm in {"weak-signal", "dormant-interest", "source-novel"}
    assert decision.hypothesis_id is not None


def test_evidence_less_hypothesis_uses_its_strategy_family_as_prior() -> None:
    decision = ThompsonAllocator(Random(11)).decide(
        intent="explore",
        hypotheses=(
            ("hyp_proven", "weak-signal", 10, 8, 2),
            ("hyp_new", "weak-signal", 0, 0, 0),
            ("hyp_other", "source-novel", 0, 0, 0),
        ),
    )

    new_sample = next(sample for sample in decision.samples if sample.hypothesis_id == "hyp_new")
    other_sample = next(
        sample for sample in decision.samples if sample.hypothesis_id == "hyp_other"
    )
    assert (new_sample.alpha, new_sample.beta) == (9, 3)
    assert (other_sample.alpha, other_sample.beta) == (1, 1)


def test_forced_exploration_requires_an_active_exploration_hypothesis() -> None:
    with pytest.raises(ValueError, match="no exploration hypotheses"):
        ThompsonAllocator(Random(1)).decide(
            intent="explore",
            hypotheses=(("exploit", "exploit", 3, 2, 1),),
        )
