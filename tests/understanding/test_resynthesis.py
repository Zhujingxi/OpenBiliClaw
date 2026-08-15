from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.observations.models import ReasonPayload, RecommendationDislikedObservation
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.understanding.evidence import EvidenceLink
from openbiliclaw.understanding.overrides import OverrideOperation, UserOverride
from openbiliclaw.understanding.profile import (
    AvoidanceClaim,
    CanonicalProfile,
    StableInterestClaim,
    claim_id,
)
from openbiliclaw.understanding.proposals import ClaimProposal, ProposalOwner
from openbiliclaw.understanding.resynthesis import (
    MAX_RESYNTHESIS_CLAIMS,
    ResynthesisDetector,
    ResynthesisService,
    ResynthesisTrigger,
)
from openbiliclaw.understanding.service import _project_observation

if TYPE_CHECKING:
    from openbiliclaw.understanding.ledger import LedgerEntry

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def evidence(index: int, *, trust: float = 0.6, age_days: int = 0) -> EvidenceLink:
    return EvidenceLink(
        evidence_id="ev_" + f"{index:032x}",
        observation_id="obs_" + f"{index:032x}",
        summary=f"evidence {index}",
        occurred_at=NOW - timedelta(days=age_days),
        trust=trust,
    )


def authenticated_dislike(index: int) -> EvidenceLink:
    event = RecommendationDislikedObservation(
        observation_id="obs_" + f"{index:032x}",
        idempotency_key=f"dislike-{index}",
        occurred_at=NOW,
        received_at=NOW,
        account_id="account-1",
        content_ref=None,
        provenance=ObservationProvenance(
            producer_id="application.feedback",
            source=ObservationSource.RECOMMENDATION,
            authenticated=True,
            trust_level=TrustLevel.HIGH,
        ),
        payload=ReasonPayload(reason="not relevant", exposed=True),
    )
    return _project_observation(event)


def interest(
    value: str, index: int, *, confidence: float = 0.8, age_days: int = 0
) -> StableInterestClaim:
    return StableInterestClaim(
        claim_id=claim_id("stable_interest", value),
        value=value,
        confidence=confidence,
        fresh_at=NOW - timedelta(days=age_days),
        evidence_ids=(evidence(index, age_days=age_days).evidence_id,),
    )


def proposal(claim: StableInterestClaim | AvoidanceClaim, *links: EvidenceLink) -> ClaimProposal:
    owner = (
        ProposalOwner.AVOIDANCE if isinstance(claim, AvoidanceClaim) else ProposalOwner.PREFERENCE
    )
    return ClaimProposal(
        proposal_id="prop_"
        + f"{len(links):016x}{sum(int(item.evidence_id[-4:], 16) for item in links):016x}",
        analyzer_id="understanding.preference.v1",
        owner=owner,
        claim=claim,
        evidence=links,
        proposed_at=NOW,
    )


class RepositoryFake:
    def __init__(self, profile: CanonicalProfile, proposals: tuple[ClaimProposal, ...]) -> None:
        self.profile = profile
        self.proposals = proposals
        self.commits: list[tuple[tuple[ClaimProposal, ...], tuple[LedgerEntry, ...]]] = []

    async def load_profile(self, profile_id: str, *, now: datetime) -> CanonicalProfile:
        return self.profile

    async def proposals_for_claims(
        self, profile_id: str, claim_ids: tuple[str, ...]
    ) -> tuple[ClaimProposal, ...]:
        return tuple(item for item in self.proposals if item.claim.claim_id in claim_ids)

    async def proposal_exists(self, proposal_id: str) -> bool:
        return any(item.proposal_id == proposal_id for item in self.proposals)

    async def commit_analysis(
        self,
        *,
        profile: CanonicalProfile,
        proposals: tuple[ClaimProposal, ...],
        decisions: tuple[LedgerEntry, ...],
        evidence: tuple[EvidenceLink, ...],
        analyzer_id: str,
        checkpoint: str,
    ) -> None:
        self.profile = profile
        self.proposals += proposals
        self.commits.append((proposals, decisions))


def avoidance(value: str, index: int, *, trust: float = 0.6) -> tuple[AvoidanceClaim, EvidenceLink]:
    link = evidence(index, trust=trust)
    return (
        AvoidanceClaim(
            claim_id=claim_id("avoidance", value),
            value=value,
            confidence=trust,
            fresh_at=NOW,
            evidence_ids=(link.evidence_id,),
        ),
        link,
    )


def test_detector_targets_only_explicitly_corrected_claim_and_conflicting_topic() -> None:
    science = interest("science", 1)
    art = interest("art", 2)
    profile = CanonicalProfile(
        profile_id="default", revision=1, updated_at=NOW, claims=(science, art)
    )
    detector = ResynthesisDetector()
    assert detector.detect(
        profile,
        ResynthesisTrigger.EXPLICIT_CORRECTION,
        edited_claim_id=science.claim_id,
        now=NOW,
    ) == (science.claim_id,)

    opposite, link = avoidance("science", 3)
    assert detector.detect(
        profile,
        ResynthesisTrigger.CONTRADICTORY_EVIDENCE,
        proposal=proposal(opposite, link),
        now=NOW,
    ) == (science.claim_id,)


def test_drift_fires_only_past_existing_staleness_boundary() -> None:
    stale = interest("stale", 1, age_days=181)
    boundary = interest("boundary", 2, age_days=180)
    fresh = interest("fresh", 3, age_days=1)
    profile = CanonicalProfile(
        profile_id="default", revision=1, updated_at=NOW, claims=(stale, boundary, fresh)
    )
    assert ResynthesisDetector().detect(profile, ResynthesisTrigger.DRIFT, now=NOW) == (
        stale.claim_id,
    )


async def test_contradiction_demotes_inference_without_touching_other_claims() -> None:
    science = interest("science", 1, confidence=0.9)
    art = interest("art", 2)
    support = evidence(1, trust=0.6)
    opposite, conflict = avoidance("science", 3, trust=0.6)
    repository = RepositoryFake(
        CanonicalProfile(profile_id="default", revision=1, updated_at=NOW, claims=(science, art)),
        (proposal(science, support), proposal(opposite, conflict)),
    )

    result = await ResynthesisService(repository, clock=lambda: NOW).resynthesize(
        "default", ResynthesisTrigger.CONTRADICTORY_EVIDENCE, (science.claim_id,)
    )

    updated = next(item for item in result.profile.claims if item.claim_id == science.claim_id)
    assert updated.confidence == 0.5
    assert next(item for item in result.profile.claims if item.claim_id == art.claim_id) == art
    assert result.claim_ids == (science.claim_id,)


async def test_explicit_user_statement_dominates_authenticated_behavioral_inference() -> None:
    science = interest("science", 1, confidence=0.9)
    explicit = evidence(1, trust=1.0)
    conflicts = tuple(authenticated_dislike(index) for index in range(2, 5))
    opposite = AvoidanceClaim(
        claim_id=claim_id("avoidance", "science"),
        value="science",
        confidence=0.8,
        fresh_at=NOW,
        evidence_ids=tuple(item.evidence_id for item in conflicts),
    )
    repository = RepositoryFake(
        CanonicalProfile(profile_id="default", revision=1, updated_at=NOW, claims=(science,)),
        (proposal(science, explicit), proposal(opposite, *conflicts)),
    )

    result = await ResynthesisService(repository, clock=lambda: NOW).resynthesize(
        "default", ResynthesisTrigger.CONTRADICTORY_EVIDENCE, (science.claim_id,)
    )

    assert {item.trust for item in conflicts} == {0.6}
    assert result.profile.claims[0].confidence == 1.0


async def test_recomputed_evidence_ids_are_capped_to_most_recent_64() -> None:
    science = interest("science", 1)
    links = tuple(evidence(index) for index in range(1, 70))
    history = tuple(proposal(science, *links[index : index + 32]) for index in range(0, 69, 32))
    repository = RepositoryFake(
        CanonicalProfile(profile_id="default", revision=1, updated_at=NOW, claims=(science,)),
        history,
    )

    result = await ResynthesisService(repository, clock=lambda: NOW).resynthesize(
        "default", ResynthesisTrigger.CONTRADICTORY_EVIDENCE, (science.claim_id,)
    )

    assert len(result.profile.claims[0].evidence_ids) == 64
    assert CanonicalProfile.model_validate_json(result.profile.model_dump_json()) == result.profile


async def test_mixed_old_and_new_history_refreshes_without_drift_livelock() -> None:
    science = interest("science", 1, age_days=181)
    old = evidence(1, age_days=181)
    new = evidence(2, age_days=1)
    repository = RepositoryFake(
        CanonicalProfile(profile_id="default", revision=1, updated_at=NOW, claims=(science,)),
        (proposal(science, old), proposal(science, new)),
    )
    service = ResynthesisService(repository, clock=lambda: NOW)

    result = await service.resynthesize("default", ResynthesisTrigger.DRIFT, (science.claim_id,))

    assert result.profile.claims[0].fresh_at == new.occurred_at
    assert ResynthesisDetector().detect(result.profile, ResynthesisTrigger.DRIFT, now=NOW) == ()
    committed, decisions = repository.commits[0]
    assert committed[0].evidence == (new,)
    assert decisions[0].reason == "resynthesis_drift"


async def test_override_rejection_is_audited_and_duplicate_proposal_is_idempotent() -> None:
    science = interest("science", 1)
    support = evidence(1)
    overridden = UserOverride.create(
        claim_id=science.claim_id,
        operation=OverrideOperation.REMOVE,
        value=None,
        created_at=NOW,
    ).apply(CanonicalProfile(profile_id="default", revision=1, updated_at=NOW, claims=(science,)))
    repository = RepositoryFake(overridden, (proposal(science, support),))
    service = ResynthesisService(repository, clock=lambda: NOW)

    first = await service.resynthesize(
        "default", ResynthesisTrigger.EXPLICIT_CORRECTION, (science.claim_id,)
    )
    committed, decisions = repository.commits[0]
    assert first.profile.claims == ()
    assert decisions[0].reason == "resynthesis_explicit_correction_override"
    assert decisions[0].status.value == "rejected"

    second = await service.resynthesize(
        "default", ResynthesisTrigger.EXPLICIT_CORRECTION, (science.claim_id,)
    )
    assert second.profile == first.profile
    assert len(repository.commits) == 1
    assert len(committed) == 1


async def test_bound_is_enforced_and_decision_is_audited() -> None:
    claims = tuple(
        interest(
            f"topic-{index}",
            index + 1,
            age_days=181 if index == 0 else 0,
        )
        for index in range(MAX_RESYNTHESIS_CLAIMS + 1)
    )
    repository = RepositoryFake(
        CanonicalProfile(profile_id="default", revision=1, updated_at=NOW, claims=claims), ()
    )
    service = ResynthesisService(repository, clock=lambda: NOW)

    with pytest.raises(ValueError, match="at most"):
        await service.resynthesize(
            "default", ResynthesisTrigger.DRIFT, tuple(item.claim_id for item in claims)
        )

    link = evidence(1, age_days=181)
    repository.proposals = (proposal(claims[0], link),)
    result = await service.resynthesize("default", ResynthesisTrigger.DRIFT, (claims[0].claim_id,))
    assert result.profile.revision == 2
    assert all(item.claim_id != claims[0].claim_id for item in result.profile.claims)
    committed_proposals, decisions = repository.commits[0]
    assert decisions[0].reason == "resynthesis_drift"
    assert committed_proposals[0].analyzer_id == "understanding.resynthesis.v1"
