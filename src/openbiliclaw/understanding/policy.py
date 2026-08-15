"""Deterministic proposal validation and conflict resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from .profile import (
    AvoidanceClaim,
    CanonicalProfile,
    EmergingInterestClaim,
    InsightClaim,
    PreferenceClaim,
    StableInterestClaim,
    claim_id,
)
from .proposals import ClaimProposal, ProposalOwner


class DecisionReason(StrEnum):
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    LOW_CONFIDENCE = "low_confidence"
    MISSING_EVIDENCE = "missing_evidence"
    STALE_EVIDENCE = "stale_evidence"
    WRONG_OWNER = "wrong_owner"
    OVERRIDE_CONFLICT = "override_conflict"
    CONTRADICTION = "contradiction"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    accepted: bool
    reason: DecisionReason
    superseded_claim_id: str | None = None


class ProposalPolicy:
    def __init__(
        self, *, minimum_confidence: float = 0.5, evidence_max_age: timedelta = timedelta(days=180)
    ) -> None:
        self._minimum_confidence = minimum_confidence
        self._evidence_max_age = evidence_max_age

    def evidence_is_stale(self, occurred_at: object, *, now: object) -> bool:
        """Use the proposal evidence horizon as the claim drift boundary."""

        from datetime import datetime

        if not isinstance(occurred_at, datetime) or not isinstance(now, datetime):
            raise TypeError("occurred_at and now must be datetime")
        return now - occurred_at > self._evidence_max_age

    def decide(
        self, profile: CanonicalProfile, proposal: ClaimProposal, *, now: object
    ) -> PolicyDecision:
        from datetime import datetime

        if not isinstance(now, datetime):
            raise TypeError("now must be datetime")
        if proposal.claim.confidence < self._minimum_confidence:
            return PolicyDecision(False, DecisionReason.LOW_CONFIDENCE)
        if not proposal.evidence:
            return PolicyDecision(False, DecisionReason.MISSING_EVIDENCE)
        if any(self.evidence_is_stale(item.occurred_at, now=now) for item in proposal.evidence):
            return PolicyDecision(False, DecisionReason.STALE_EVIDENCE)
        if proposal.owner is not _owner_for(proposal.claim):
            return PolicyDecision(False, DecisionReason.WRONG_OWNER)
        if any(item.claim_id == proposal.claim.claim_id for item in profile.overrides):
            return PolicyDecision(False, DecisionReason.OVERRIDE_CONFLICT)
        if _contradicts(profile, proposal):
            return PolicyDecision(False, DecisionReason.CONTRADICTION)
        existing = next(
            (item for item in profile.claims if item.claim_id == proposal.claim.claim_id), None
        )
        if existing is not None:
            return PolicyDecision(True, DecisionReason.SUPERSEDED, existing.claim_id)
        return PolicyDecision(True, DecisionReason.ACCEPTED)


def _owner_for(
    claim: StableInterestClaim
    | EmergingInterestClaim
    | AvoidanceClaim
    | PreferenceClaim
    | InsightClaim,
) -> ProposalOwner:
    match claim:
        case StableInterestClaim() | PreferenceClaim():
            return ProposalOwner.PREFERENCE
        case EmergingInterestClaim():
            return ProposalOwner.TOPIC_LIFECYCLE
        case AvoidanceClaim():
            return ProposalOwner.AVOIDANCE
        case InsightClaim():
            return ProposalOwner.INSIGHT


def _contradicts(profile: CanonicalProfile, proposal: ClaimProposal) -> bool:
    candidate = proposal.claim
    if not isinstance(candidate, (StableInterestClaim, AvoidanceClaim)):
        return False
    opposite = AvoidanceClaim if isinstance(candidate, StableInterestClaim) else StableInterestClaim
    if any(
        isinstance(item, opposite) and item.value.casefold() == candidate.value.casefold()
        for item in profile.claims
    ):
        return True
    # An override that removed the opposite claim must still block its value:
    # the claim row is gone but the user's decision must not be reintroduced
    # by inference under a different claim_id.
    opposite_kind = "avoidance" if isinstance(candidate, StableInterestClaim) else "stable_interest"
    return any(
        item.claim_id == claim_id(opposite_kind, candidate.value) for item in profile.overrides
    )
