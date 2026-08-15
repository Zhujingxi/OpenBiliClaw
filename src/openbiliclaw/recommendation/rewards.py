"""Pinned reward vocabulary and feedback-to-exploration credit mapping.

Observable signals today are explicit satisfaction (like/save), explicit correction
(dislike/dismiss), and weak meaningful consumption (open, weight 0.25). Voluntary return
and repetition fatigue are reserved derived outcomes: they need longitudinal evidence
and are not emitted here. Only like/save resolves an exploration attempt as success;
dislike/dismiss resolves it as failure. Open records an attempt but no Beta resolution.

``RewardLedger.record`` requires the durable ``ShownRecord`` matching the feedback. The
current delivery pipeline treats newly accepted feedback on that record as proof of
exposure; callers invoke it only after the idempotent feedback insert succeeds.
Dismissal resolution requires viewport exposure; other accepted feedback still proves
an attempt. When composition supplies the standing exploit hypothesis, unattributed
open/like/save update its funnel
(and like/save posterior); correction never alters exploit or profile state.
Like/save on exploration supply may additionally enter Understanding through composition.
Channel-attributed exploit supply resolves a standing channel hypothesis through the same ledger.
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol, assert_never

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.recommendation.models import (
    DiscoveryProvenance,
    ExplorationAttribution,
    FeedbackKind,
    FeedbackRecord,
    ShownRecord,
)

if TYPE_CHECKING:
    from datetime import datetime

    from openbiliclaw.recommendation.hypotheses import HypothesisRegistry
    from openbiliclaw.recommendation.policy_journal import JournalOutcome, OutcomeKind

__all__ = [
    "ExplorationAttribution",
    "RewardKind",
    "RewardLedger",
    "RewardSignal",
    "map_feedback",
    "record_supply_reward",
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


def _channel_arm(provider: str, channel: str) -> tuple[str, str]:
    # ponytail: hyphen-joined slug is ambiguous for hyphenated provider ids
    # (a-b + c collides with a + b-c); no such ids exist today — switch the join
    # separator if one is added.
    channel_ref = channel if channel.startswith(f"{provider}:") else f"{provider}:{channel}"
    feed_id = channel_ref.removeprefix(f"{provider}:")
    readable = f"channel-{provider}-{feed_id}"
    if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", readable):
        return readable, channel_ref
    digest = hashlib.sha256(channel_ref.encode()).hexdigest()[:32]
    return f"channel-{digest}", channel_ref


async def record_supply_reward(
    registry: HypothesisRegistry,
    feedback: FeedbackRecord,
    shown: ShownRecord,
    provenance: DiscoveryProvenance,
    *,
    now: datetime,
) -> RewardSignal:
    """Credit feedback to its exploration, provider channel, or exploit supply arm."""

    attribution = provenance.exploration
    exploration_signal = (
        await RewardLedger(registry).record(feedback, shown, attribution)
        if attribution is not None
        else None
    )
    if provenance.channel is not None:
        arm, channel_ref = _channel_arm(provenance.provider, provenance.channel)
        hypothesis = await registry.ensure_active(
            arm=arm,
            statement=f"Provider channel {channel_ref} may yield satisfying recommendations",
            evidence_refs=(f"channel:{channel_ref}",),
            falsification="resolved failures exceed resolved successes",
            expires_at=now + timedelta(days=365),
            now=now,
        )
        channel_credit = ExplorationAttribution(
            hypothesis_id=hypothesis.hypothesis_id,
            arm=arm,
            channel=channel_ref,
        )
        channel_signal = await RewardLedger(registry).record(feedback, shown, channel_credit)
        return exploration_signal or channel_signal
    if exploration_signal is not None:
        return exploration_signal

    exploit_id = None
    if feedback.kind in (FeedbackKind.OPENED, FeedbackKind.LIKED, FeedbackKind.SAVED):
        exploit = await registry.ensure_active(
            arm="exploit",
            statement="The familiar exploit strategy may satisfy current intent",
            evidence_refs=("system:feedback",),
            falsification="resolved failures exceed resolved successes",
            expires_at=now + timedelta(days=365),
            now=now,
        )
        exploit_id = exploit.hypothesis_id
    return await RewardLedger(registry, exploit_hypothesis_id=exploit_id).record(
        feedback, shown, None
    )


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
        if feedback.kind is FeedbackKind.DISMISSED and not feedback.exposed:
            resolution = None
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
        if resolution != signal.resolution:
            signal = signal.model_copy(update={"resolution": resolution})
        return signal
