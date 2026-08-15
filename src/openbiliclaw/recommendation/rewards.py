"""Pinned reward vocabulary and feedback-to-exploration credit mapping.

Observable signals today are explicit satisfaction (like/save), explicit correction
(dislike/dismiss), and weak meaningful consumption (open, weight 0.25). Voluntary return
and repetition fatigue are reserved derived outcomes: they need longitudinal evidence
and are not emitted here. Only like/save resolves an exploration attempt as success;
dislike/dismiss resolves it as failure. Open records an attempt but no Beta resolution.

``RewardLedger.record`` requires the durable ``ShownRecord`` matching the feedback. The
current delivery pipeline treats newly accepted feedback on that record as proof of
exposure; callers invoke it only after the idempotent feedback insert succeeds.
Viewport-level exposure evidence replaces that assumption in Phase B5. When composition
supplies the standing exploit hypothesis, unattributed open/like/save update its funnel
(and like/save posterior); correction never alters exploit or profile state.
Like-on-explore understanding proposals are also Phase B5 scope.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol, assert_never

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.recommendation.models import (
    ExplorationAttribution,
    FeedbackKind,
    FeedbackRecord,
    ShownRecord,
)

if TYPE_CHECKING:
    from openbiliclaw.recommendation.policy_journal import JournalOutcome, OutcomeKind

__all__ = [
    "ExplorationAttribution",
    "RewardKind",
    "RewardLedger",
    "RewardSignal",
    "map_feedback",
]


class RewardKind(StrEnum):
    """Multi-objective outcome vocabulary.

    Explicit satisfaction/correction and meaningful consumption are observable now.
    Voluntary return and repetition fatigue are derived later from longitudinal evidence.
    """

    EXPLICIT_SATISFACTION = "explicit-satisfaction"  # direct like or save
    EXPLICIT_CORRECTION = "explicit-correction"  # direct dislike or dismiss
    MEANINGFUL_CONSUMPTION = "meaningful-consumption"  # weak open signal
    VOLUNTARY_RETURN = "voluntary-return"  # derived from later return evidence
    REPETITION_FATIGUE = "repetition-fatigue"  # derived from longitudinal evidence


class RewardSignal(StrictBaseModel):
    """One mapped feedback signal and its optional exploration-only credit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reward_kind: RewardKind
    hypothesis_credit_target: ExplorationAttribution | None = None
    weight: float = Field(ge=-1, le=1)
    resolution: Literal["success", "failure"] | None = None


def map_feedback(kind: FeedbackKind, attribution: ExplorationAttribution | None) -> RewardSignal:
    """Map feedback without side effects; the configured ledger owns exploit credit."""

    if kind is FeedbackKind.OPENED:
        return RewardSignal(reward_kind=RewardKind.MEANINGFUL_CONSUMPTION, weight=0.25)
    if kind is FeedbackKind.LIKED or kind is FeedbackKind.SAVED:
        return RewardSignal(
            reward_kind=RewardKind.EXPLICIT_SATISFACTION,
            hypothesis_credit_target=attribution,
            weight=1.0,
            resolution="success" if attribution is not None else None,
        )
    if kind is FeedbackKind.DISLIKED or kind is FeedbackKind.DISMISSED:
        return RewardSignal(
            reward_kind=RewardKind.EXPLICIT_CORRECTION,
            hypothesis_credit_target=attribution,
            weight=-1.0,
            resolution="failure" if attribution is not None else None,
        )
    assert_never(kind)


class _OutcomeRegistry(Protocol):
    async def record_outcome(
        self, hypothesis_id: str, kind: OutcomeKind, detail: str
    ) -> JournalOutcome: ...


class RewardLedger:
    """Credit one newly accepted feedback event through ``HypothesisRegistry``."""

    def __init__(
        self,
        registry: _OutcomeRegistry,
        *,
        exploit_hypothesis_id: str | None = None,
    ) -> None:
        self._registry = registry
        self._exploit_hypothesis_id = exploit_hypothesis_id

    async def record(
        self,
        feedback: FeedbackRecord,
        shown: ShownRecord,
        attribution: ExplorationAttribution | None,
    ) -> RewardSignal:
        """Map feedback and credit its supplying hypothesis or configured exploit sink."""

        if feedback.shown_id != shown.shown_id:
            raise ValueError("feedback does not match shown record")
        # Late feedback on a hypothesis killed between serve and feedback raises
        # ValueError from the registry (killed is terminal; no partial write occurs).
        signal = map_feedback(feedback.kind, attribution)
        target_id = (
            attribution.hypothesis_id
            if attribution is not None
            else (
                self._exploit_hypothesis_id
                if feedback.kind in (FeedbackKind.OPENED, FeedbackKind.LIKED, FeedbackKind.SAVED)
                else None
            )
        )
        if target_id is None:
            return signal

        await self._registry.record_outcome(
            target_id,
            "attempt",
            f"shown {shown.shown_id}: exposure inferred from accepted feedback",
        )
        resolution = signal.resolution
        if attribution is None and (
            feedback.kind is FeedbackKind.LIKED or feedback.kind is FeedbackKind.SAVED
        ):
            resolution = "success"
        if resolution is not None:
            await self._registry.record_outcome(
                target_id,
                resolution,
                f"shown {shown.shown_id}: {feedback.kind.value}",
            )
        return signal
