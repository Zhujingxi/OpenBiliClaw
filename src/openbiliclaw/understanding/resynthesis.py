"""Event-triggered, bounded recomputation of existing profile claims."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel

from .policy import ProposalPolicy
from .profile import (
    AvoidanceClaim,
    CanonicalProfile,
    EmergingInterestClaim,
    InsightClaim,
    PreferenceClaim,
    ProfileClaim,
    StableInterestClaim,
    claim_id,
)
from .proposals import ClaimProposal, ProposalOwner
from .service import apply_proposals

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from .evidence import EvidenceLink
    from .ledger import LedgerEntry

# ponytail: one event recomputes at most this many claims; later events drain drift backlog.
MAX_RESYNTHESIS_CLAIMS = 25
_RESYNTHESIS_ANALYZER = "understanding.resynthesis.v1"


class ResynthesisTrigger(StrEnum):
    EXPLICIT_CORRECTION = "explicit-correction"
    CONTRADICTORY_EVIDENCE = "contradictory-evidence"
    DRIFT = "drift"


class ResynthesisResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: CanonicalProfile
    claim_ids: tuple[str, ...] = Field(max_length=MAX_RESYNTHESIS_CLAIMS)


class ResynthesisRepository(Protocol):
    async def load_profile(self, profile_id: str, *, now: datetime) -> CanonicalProfile: ...
    async def proposals_for_claims(
        self, profile_id: str, claim_ids: tuple[str, ...]
    ) -> tuple[ClaimProposal, ...]: ...
    async def proposal_exists(self, proposal_id: str) -> bool: ...
    async def commit_analysis(
        self,
        *,
        profile: CanonicalProfile,
        proposals: tuple[ClaimProposal, ...],
        decisions: tuple[LedgerEntry, ...],
        evidence: tuple[EvidenceLink, ...],
        analyzer_id: str,
        checkpoint: str,
    ) -> None: ...


class ResynthesisDetector:
    """Map one committed event to only the claims it can affect."""

    def __init__(self, policy: ProposalPolicy | None = None) -> None:
        self._policy = policy or ProposalPolicy()

    def detect(
        self,
        profile: CanonicalProfile,
        trigger: ResynthesisTrigger,
        *,
        now: datetime,
        edited_claim_id: str | None = None,
        proposal: ClaimProposal | None = None,
    ) -> tuple[str, ...]:
        if trigger is ResynthesisTrigger.EXPLICIT_CORRECTION:
            if edited_claim_id is None:
                raise ValueError("explicit correction requires edited_claim_id")
            return (edited_claim_id,)
        if trigger is ResynthesisTrigger.CONTRADICTORY_EVIDENCE:
            if proposal is None:
                raise ValueError("contradictory evidence requires proposal")
            opposite = _opposite_identity(proposal.claim)
            if opposite is None:
                return ()
            return tuple(item.claim_id for item in profile.claims if item.claim_id == opposite)
        return tuple(
            item.claim_id
            for item in profile.claims
            if self._policy.evidence_is_stale(item.fresh_at, now=now)
        )


class ResynthesisService:
    """Recompute affected claims from their durable proposal/evidence history."""

    def __init__(
        self,
        repository: ResynthesisRepository,
        *,
        clock: Callable[[], datetime],
        policy: ProposalPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._policy = policy or ProposalPolicy()
        self._detector = ResynthesisDetector(self._policy)

    async def after_proposals(
        self, profile_id: str, proposals: tuple[ClaimProposal, ...]
    ) -> ResynthesisResult:
        """Evaluate contradiction and drift immediately after an analysis commit."""

        now = self._clock()
        profile = await self._repository.load_profile(profile_id, now=now)
        contradictory = tuple(
            dict.fromkeys(
                claim_identity
                for proposal in proposals
                for claim_identity in self._detector.detect(
                    profile,
                    ResynthesisTrigger.CONTRADICTORY_EVIDENCE,
                    proposal=proposal,
                    now=now,
                )
            )
        )[:MAX_RESYNTHESIS_CLAIMS]
        processed: list[str] = []
        if contradictory:
            result = await self.resynthesize(
                profile_id, ResynthesisTrigger.CONTRADICTORY_EVIDENCE, contradictory
            )
            profile = result.profile
            processed.extend(result.claim_ids)
        remaining = MAX_RESYNTHESIS_CLAIMS - len(processed)
        drifted = self._detector.detect(profile, ResynthesisTrigger.DRIFT, now=now)[:remaining]
        if drifted:
            result = await self.resynthesize(profile_id, ResynthesisTrigger.DRIFT, drifted)
            profile = result.profile
            processed.extend(result.claim_ids)
        return ResynthesisResult(profile=profile, claim_ids=tuple(dict.fromkeys(processed)))

    async def resynthesize(
        self,
        profile_id: str,
        trigger: ResynthesisTrigger,
        claim_ids: tuple[str, ...],
    ) -> ResynthesisResult:
        """Recompute at most ``MAX_RESYNTHESIS_CLAIMS`` without scanning other claims."""

        unique_ids = tuple(dict.fromkeys(claim_ids))
        if len(unique_ids) > MAX_RESYNTHESIS_CLAIMS:
            raise ValueError(f"resynthesis accepts at most {MAX_RESYNTHESIS_CLAIMS} claims")
        now = self._clock()
        profile = await self._repository.load_profile(profile_id, now=now)
        output_proposals: list[ClaimProposal] = []

        for identity in unique_ids:
            current = next((item for item in profile.claims if item.claim_id == identity), None)
            if current is None:
                history = await self._repository.proposals_for_claims(profile_id, (identity,))
                source = history[-1].claim if history else None
            else:
                source = current
                history = ()
            if source is None:
                continue
            related_ids: tuple[str, ...] = (identity,)
            opposite = _opposite_identity(source)
            if opposite is not None:
                related_ids += (opposite,)
            if current is not None or len(related_ids) > 1:
                history = await self._repository.proposals_for_claims(profile_id, related_ids)
            candidate = _recompute(source, identity, history, now=now)
            if trigger is ResynthesisTrigger.DRIFT and self._policy.evidence_is_stale(
                candidate.fresh_at, now=now
            ):
                candidate = candidate.model_copy(update={"confidence": 0.0})
            proposal_id = _proposal_identity(trigger, candidate)
            if await self._repository.proposal_exists(proposal_id):
                continue
            resynthesis_proposal = ClaimProposal(
                proposal_id=proposal_id,
                analyzer_id=_RESYNTHESIS_ANALYZER,
                owner=_owner_for(candidate),
                claim=candidate,
                evidence=_proposal_evidence(identity, history, policy=self._policy, now=now),
                proposed_at=now,
            )
            output_proposals.append(resynthesis_proposal)

        reason = f"resynthesis_{trigger.value.replace('-', '_')}"
        updated, decisions, _accepted, _rejected = apply_proposals(
            profile,
            tuple(output_proposals),
            now=now,
            policy=self._policy,
            resynthesis_reason=reason,
        )
        if decisions:
            evidence_by_id = {
                item.evidence_id: item
                for proposal in output_proposals
                for item in proposal.evidence
            }
            evidence = tuple(evidence_by_id.values())
            await self._repository.commit_analysis(
                profile=updated,
                proposals=tuple(output_proposals),
                decisions=tuple(decisions),
                evidence=evidence,
                analyzer_id=_RESYNTHESIS_ANALYZER,
                checkpoint="0",
            )
        return ResynthesisResult(profile=updated, claim_ids=unique_ids)


def _opposite_identity(claim: ProfileClaim) -> str | None:
    if isinstance(claim, StableInterestClaim):
        return claim_id("avoidance", claim.value)
    if isinstance(claim, AvoidanceClaim):
        return claim_id("stable_interest", claim.value)
    return None


def _owner_for(claim: ProfileClaim) -> ProposalOwner:
    if isinstance(claim, (StableInterestClaim, PreferenceClaim)):
        return ProposalOwner.PREFERENCE
    if isinstance(claim, AvoidanceClaim):
        return ProposalOwner.AVOIDANCE
    if isinstance(claim, EmergingInterestClaim):
        return ProposalOwner.TOPIC_LIFECYCLE
    if isinstance(claim, InsightClaim):
        return ProposalOwner.INSIGHT
    raise TypeError("unsupported profile claim")


def _all_evidence(identity: str, history: tuple[ClaimProposal, ...]) -> tuple[EvidenceLink, ...]:
    links = {
        item.evidence_id: item
        for proposal in history
        for item in proposal.evidence
        if proposal.claim.claim_id == identity or _opposite_identity(proposal.claim) == identity
    }
    return tuple(sorted(links.values(), key=lambda item: (item.occurred_at, item.evidence_id)))


def _proposal_evidence(
    identity: str,
    history: tuple[ClaimProposal, ...],
    *,
    policy: ProposalPolicy,
    now: datetime,
) -> tuple[EvidenceLink, ...]:
    return tuple(
        item
        for item in _all_evidence(identity, history)
        if not policy.evidence_is_stale(item.occurred_at, now=now)
    )[-64:]


def _recompute(
    source: ProfileClaim,
    identity: str,
    history: tuple[ClaimProposal, ...],
    *,
    now: datetime,
) -> ProfileClaim:
    links = _all_evidence(identity, history)
    explicit = tuple(item for item in links if item.trust == 1.0)
    weighted_links = explicit or links
    used_links = weighted_links[-64:]
    weighted_ids = {item.evidence_id for item in weighted_links}
    support_links = {
        item.evidence_id: item
        for proposal in history
        if proposal.claim.claim_id == identity
        for item in proposal.evidence
        if item.evidence_id in weighted_ids
    }
    conflict_links = {
        item.evidence_id: item
        for proposal in history
        if _opposite_identity(proposal.claim) == identity
        for item in proposal.evidence
        if item.evidence_id in weighted_ids
    }
    support = sum(item.trust for item in support_links.values())
    conflict = sum(item.trust for item in conflict_links.values())
    confidence = support / (support + conflict) if support + conflict else 0.0
    fresh_at = max((item.occurred_at for item in used_links), default=now)
    evidence_ids = tuple(item.evidence_id for item in used_links) or source.evidence_ids
    return source.model_copy(
        update={"confidence": confidence, "fresh_at": fresh_at, "evidence_ids": evidence_ids}
    )


def _proposal_identity(trigger: ResynthesisTrigger, claim: ProfileClaim) -> str:
    raw = ":".join(
        (
            trigger.value,
            claim.claim_id,
            str(claim.confidence),
            *claim.evidence_ids,
        )
    )
    return "prop_" + hashlib.sha256(raw.encode()).hexdigest()[:32]
