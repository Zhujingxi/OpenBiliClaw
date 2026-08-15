"""Reward vocabulary, pure mapping, and exploration-ledger contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator
from openbiliclaw.recommendation.hypotheses import HypothesisRegistry
from openbiliclaw.recommendation.models import (
    DiscoveryProvenance,
    FeedbackKind,
    FeedbackRecord,
    ShownRecord,
)
from openbiliclaw.recommendation.policy_journal import SqlitePolicyJournal
from openbiliclaw.recommendation.rewards import (
    ExplorationAttribution,
    RewardKind,
    RewardLedger,
    map_feedback,
    record_supply_reward,
)

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.recommendation.policy_journal import JournalOutcome, OutcomeKind

NOW = datetime(2026, 8, 15, tzinfo=UTC)
ATTRIBUTION = ExplorationAttribution(
    hypothesis_id="hyp_" + "a" * 32,
    arm="source-novel",
    channel="bilibili:popular",
)


class RecordingRegistry:
    def __init__(self) -> None:
        self.outcomes: list[tuple[str, OutcomeKind, str]] = []

    async def record_outcome(
        self, hypothesis_id: str, kind: OutcomeKind, detail: str
    ) -> JournalOutcome:
        self.outcomes.append((hypothesis_id, kind, detail))
        return None  # type: ignore[return-value]


def _shown() -> ShownRecord:
    return ShownRecord(
        shown_id="shown_" + "c" * 32,
        recommendation_id="rec_" + "d" * 32,
        candidate_id="cand_" + "e" * 32,
        shown_at=NOW,
    )


def _feedback(kind: FeedbackKind, *, exposed: bool = True) -> FeedbackRecord:
    return FeedbackRecord(
        feedback_id="feedback_" + "b" * 32,
        shown_id=_shown().shown_id,
        kind=kind,
        occurred_at=NOW,
        exposed=exposed,
    )


@pytest.mark.parametrize(
    ("feedback", "reward", "weight", "resolution"),
    [
        (FeedbackKind.OPENED, RewardKind.MEANINGFUL_CONSUMPTION, 0.25, None),
        (FeedbackKind.LIKED, RewardKind.EXPLICIT_SATISFACTION, 1.0, "success"),
        (FeedbackKind.SAVED, RewardKind.EXPLICIT_SATISFACTION, 1.0, "success"),
        (FeedbackKind.DISLIKED, RewardKind.EXPLICIT_CORRECTION, -1.0, "failure"),
        (FeedbackKind.DISMISSED, RewardKind.EXPLICIT_CORRECTION, -1.0, "failure"),
    ],
)
def test_each_feedback_kind_has_a_pinned_reward_mapping(
    feedback: FeedbackKind,
    reward: RewardKind,
    weight: float,
    resolution: str | None,
) -> None:
    signal = map_feedback(feedback, ATTRIBUTION)

    assert signal.reward_kind is reward
    assert signal.weight == weight
    assert signal.resolution == resolution
    if resolution:
        assert signal.hypothesis_credit_target == ATTRIBUTION
    else:
        assert signal.hypothesis_credit_target is None


def test_reward_vocabulary_includes_observable_and_deferred_derived_outcomes() -> None:
    assert set(RewardKind) == {
        RewardKind.EXPLICIT_SATISFACTION,
        RewardKind.EXPLICIT_CORRECTION,
        RewardKind.MEANINGFUL_CONSUMPTION,
        RewardKind.VOLUNTARY_RETURN,
        RewardKind.REPETITION_FATIGUE,
    }


def test_exploration_credit_targets_only_the_supplying_arm() -> None:
    signal = map_feedback(FeedbackKind.LIKED, ATTRIBUTION)

    assert signal.hypothesis_credit_target == ATTRIBUTION
    assert signal.hypothesis_credit_target.arm == "source-novel"


def test_dismiss_never_decays_an_exploit_arm_or_profile() -> None:
    signal = map_feedback(FeedbackKind.DISMISSED, None)

    assert signal.reward_kind is RewardKind.EXPLICIT_CORRECTION
    assert signal.weight == -1.0
    assert signal.hypothesis_credit_target is None
    assert signal.resolution is None


async def test_unattributed_feedback_returns_guardrail_signal_without_outcome() -> None:
    registry = RecordingRegistry()

    signal = await RewardLedger(registry).record(_feedback(FeedbackKind.LIKED), _shown(), None)

    assert signal.reward_kind is RewardKind.EXPLICIT_SATISFACTION
    assert signal.weight == 1.0
    assert registry.outcomes == []


async def test_unattributed_dismiss_never_updates_configured_exploit_arm() -> None:
    registry = RecordingRegistry()

    await RewardLedger(registry, exploit_hypothesis_id="hyp_" + "f" * 32).record(
        _feedback(FeedbackKind.DISMISSED), _shown(), None
    )

    assert registry.outcomes == []


async def test_unexposed_exploration_dismiss_records_attempt_without_failure() -> None:
    registry = RecordingRegistry()

    await RewardLedger(registry).record(
        _feedback(FeedbackKind.DISMISSED, exposed=False), _shown(), ATTRIBUTION
    )

    assert [outcome[1] for outcome in registry.outcomes] == ["attempt"]


async def test_exposed_exploration_dismiss_records_failure() -> None:
    registry = RecordingRegistry()

    await RewardLedger(registry).record(
        _feedback(FeedbackKind.DISMISSED, exposed=True), _shown(), ATTRIBUTION
    )

    assert [outcome[1] for outcome in registry.outcomes] == ["attempt", "failure"]


async def test_unattributed_feedback_updates_configured_exploit_posterior() -> None:
    registry = RecordingRegistry()
    exploit_id = "hyp_" + "f" * 32

    await RewardLedger(registry, exploit_hypothesis_id=exploit_id).record(
        _feedback(FeedbackKind.LIKED), _shown(), None
    )

    assert [row[:2] for row in registry.outcomes] == [
        (exploit_id, "attempt"),
        (exploit_id, "success"),
    ]


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (FeedbackKind.OPENED, ["attempt"]),
        (FeedbackKind.LIKED, ["attempt", "success"]),
        (FeedbackKind.SAVED, ["attempt", "success"]),
        (FeedbackKind.DISLIKED, ["attempt", "failure"]),
        (FeedbackKind.DISMISSED, ["attempt", "failure"]),
    ],
)
async def test_exposure_is_logged_before_any_resolution(
    kind: FeedbackKind, expected: list[str]
) -> None:
    registry = RecordingRegistry()

    await RewardLedger(registry).record(_feedback(kind), _shown(), ATTRIBUTION)

    assert [outcome[1] for outcome in registry.outcomes] == expected
    assert {outcome[0] for outcome in registry.outcomes} == {ATTRIBUTION.hypothesis_id}
    assert all(_feedback(kind).shown_id in outcome[2] for outcome in registry.outcomes)


async def test_exploit_feedback_updates_persisted_exploit_posterior(tmp_path: Path) -> None:
    path = tmp_path / "exploit-rewards.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    try:
        registry = HypothesisRegistry(SqlitePolicyJournal(database), clock=lambda: NOW)
        exploit = await registry.ensure_active(
            arm="exploit",
            statement="The familiar strategy may satisfy current intent",
            evidence_refs=("system:feedback",),
            falsification="resolved failures exceed successes",
            expires_at=NOW + timedelta(days=7),
            now=NOW,
        )

        await RewardLedger(registry, exploit_hypothesis_id=exploit.hypothesis_id).record(
            _feedback(FeedbackKind.LIKED), _shown(), None
        )

        assert await registry.posterior(exploit.hypothesis_id) == (1, 1, 0)
    finally:
        await database.close()


async def test_channel_attributed_feedback_updates_its_channel_arm(tmp_path: Path) -> None:
    path = tmp_path / "channel-rewards.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    try:
        journal = SqlitePolicyJournal(database)
        registry = HypothesisRegistry(journal, clock=lambda: NOW)
        provenance = DiscoveryProvenance(
            strategy_id="provider.feed",
            query_key="bilibili:rcmd:BV1RCMD12345",
            provider="bilibili",
            channel="bilibili:rcmd",
            exploration=None,
            discovered_at=NOW,
        )

        await record_supply_reward(
            registry,
            _feedback(FeedbackKind.LIKED),
            _shown(),
            provenance,
            now=NOW,
        )

        channel = next(
            item for item in await registry.active(NOW) if item.arm == "channel-bilibili-rcmd"
        )
        assert channel.evidence_refs == ("channel:bilibili:rcmd",)
        assert await registry.posterior(channel.hypothesis_id) == (1, 1, 0)
        assert provenance.exploration is None

        exploration = await registry.ensure_active(
            arm="source-novel",
            statement="Cross-provider novelty may satisfy the user",
            evidence_refs=("system:test",),
            falsification="resolved failures exceed successes",
            expires_at=NOW + timedelta(days=1),
            now=NOW,
        )
        await record_supply_reward(
            registry,
            _feedback(FeedbackKind.LIKED),
            _shown(),
            provenance.model_copy(
                update={
                    "channel": "bilibili:popular",
                    "exploration": ATTRIBUTION.model_copy(
                        update={
                            "hypothesis_id": exploration.hypothesis_id,
                            "channel": "bilibili:popular",
                        }
                    ),
                }
            ),
            now=NOW,
        )
        popular = next(
            item for item in await registry.active(NOW) if item.arm == "channel-bilibili-popular"
        )
        assert await registry.posterior(exploration.hypothesis_id) == (1, 1, 0)
        assert await registry.posterior(popular.hypothesis_id) == (1, 1, 0)
    finally:
        await database.close()


async def test_exposure_precedes_resolution_in_the_policy_journal(tmp_path: Path) -> None:
    path = tmp_path / "rewards.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    try:
        journal = SqlitePolicyJournal(database)
        registry = HypothesisRegistry(journal, clock=lambda: NOW)
        hypothesis = await registry.register(
            arm=ATTRIBUTION.arm,
            statement="Novel-source supply may satisfy the user",
            evidence_refs=("obs_opaque",),
            falsification="two resolved failures",
            expires_at=NOW + timedelta(days=7),
        )
        attribution = ATTRIBUTION.model_copy(update={"hypothesis_id": hypothesis.hypothesis_id})

        await RewardLedger(registry).record(_feedback(FeedbackKind.LIKED), _shown(), attribution)

        outcomes = await journal.list_outcomes(hypothesis.hypothesis_id)
        assert [outcome.kind for outcome in outcomes] == ["attempt", "success"]
    finally:
        await database.close()


async def test_feedback_requires_its_durable_shown_record() -> None:
    registry = RecordingRegistry()
    other = _shown().model_copy(update={"shown_id": "shown_" + "f" * 32})

    with pytest.raises(ValueError, match="does not match shown record"):
        await RewardLedger(registry).record(_feedback(FeedbackKind.DISMISSED), other, ATTRIBUTION)

    assert registry.outcomes == []
