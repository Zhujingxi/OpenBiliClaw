from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from openbiliclaw.understanding.evidence import EvidenceLink
from openbiliclaw.understanding.overrides import OverrideOperation, UserOverride
from openbiliclaw.understanding.policy import DecisionReason, ProposalPolicy
from openbiliclaw.understanding.profile import (
    AvoidanceClaim,
    CanonicalProfile,
    ClaimLifecycle,
    EmergingInterestClaim,
    InsightClaim,
    PreferenceClaim,
    PreferenceDimension,
    StableInterestClaim,
    claim_id,
)
from openbiliclaw.understanding.proposals import ClaimProposal, ProposalOwner

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def evidence(index: int = 1, *, occurred_at: datetime = NOW) -> EvidenceLink:
    return EvidenceLink(
        evidence_id="ev_" + f"{index:032x}",
        observation_id="obs_" + f"{index:032x}",
        summary="User explicitly liked this topic",
        occurred_at=occurred_at,
        trust=1.0,
    )


def interest(value: str = "science", *, confidence: float = 0.8) -> StableInterestClaim:
    return StableInterestClaim(
        claim_id=claim_id("stable_interest", value),
        value=value,
        confidence=confidence,
        fresh_at=NOW,
        evidence_ids=(evidence().evidence_id,),
    )


def proposal(
    claim: StableInterestClaim
    | AvoidanceClaim
    | EmergingInterestClaim
    | PreferenceClaim
    | InsightClaim,
    *,
    owner: ProposalOwner = ProposalOwner.PREFERENCE,
    links: tuple[EvidenceLink, ...] = (evidence(),),
) -> ClaimProposal:
    return ClaimProposal(
        proposal_id="prop_" + "a" * 32,
        analyzer_id="understanding.preference.v1",
        owner=owner,
        claim=claim,
        evidence=links,
        proposed_at=NOW,
    )


def test_claim_variants_and_deterministic_identity_invariants() -> None:
    claims = (
        interest(),
        AvoidanceClaim(
            claim_id=claim_id("avoidance", "spoilers"),
            value="spoilers",
            confidence=0.9,
            fresh_at=NOW,
            evidence_ids=(evidence().evidence_id,),
        ),
        EmergingInterestClaim(
            claim_id=claim_id("emerging_interest", "rust"),
            value="rust",
            confidence=0.6,
            fresh_at=NOW,
            evidence_ids=(evidence().evidence_id,),
        ),
        PreferenceClaim(
            claim_id=claim_id("preference", "language:en"),
            dimension=PreferenceDimension.LANGUAGE,
            value="en",
            confidence=0.8,
            fresh_at=NOW,
            evidence_ids=(evidence().evidence_id,),
        ),
        InsightClaim(
            claim_id=claim_id("insight", "Prefers practical explanations"),
            value="Prefers practical explanations",
            confidence=0.7,
            fresh_at=NOW,
            evidence_ids=(evidence().evidence_id,),
        ),
    )
    profile = CanonicalProfile(profile_id="default", revision=1, updated_at=NOW, claims=claims)
    assert len(profile.claims) == 5
    assert claim_id("stable_interest", " Science ") == claim_id("stable_interest", "science")
    with pytest.raises(ValidationError, match="deterministic"):
        StableInterestClaim(
            claim_id="claim_" + "0" * 32,
            value="science",
            confidence=0.8,
            fresh_at=NOW,
            evidence_ids=(evidence().evidence_id,),
        )
    with pytest.raises(ValidationError, match="duplicate"):
        CanonicalProfile(
            profile_id="default", revision=1, updated_at=NOW, claims=(interest(), interest())
        )


@pytest.mark.parametrize(
    ("candidate", "owner", "links", "now", "reason"),
    [
        (
            interest(confidence=0.49),
            ProposalOwner.PREFERENCE,
            (evidence(),),
            NOW,
            DecisionReason.LOW_CONFIDENCE,
        ),
        (interest(), ProposalOwner.PREFERENCE, (), NOW, DecisionReason.MISSING_EVIDENCE),
        (
            interest(),
            ProposalOwner.PREFERENCE,
            (evidence(1, occurred_at=NOW - timedelta(days=181)),),
            NOW,
            DecisionReason.STALE_EVIDENCE,
        ),
        (interest(), ProposalOwner.AVOIDANCE, (evidence(),), NOW, DecisionReason.WRONG_OWNER),
    ],
)
def test_policy_rejects_invalid_proposals(
    candidate: StableInterestClaim,
    owner: ProposalOwner,
    links: tuple[EvidenceLink, ...],
    now: datetime,
    reason: DecisionReason,
) -> None:
    result = ProposalPolicy().decide(
        CanonicalProfile.empty("default", now),
        proposal(candidate, owner=owner, links=links),
        now=now,
    )
    assert not result.accepted
    assert result.reason is reason


def test_policy_override_conflict_and_inferred_contradiction() -> None:
    override = UserOverride(
        override_id="override_" + "a" * 32,
        claim_id=claim_id("stable_interest", "science"),
        operation=OverrideOperation.REMOVE,
        value=None,
        created_at=NOW,
    )
    profile = CanonicalProfile(
        profile_id="default", revision=1, updated_at=NOW, overrides=(override,)
    )
    assert (
        ProposalPolicy().decide(profile, proposal(interest()), now=NOW).reason
        is DecisionReason.OVERRIDE_CONFLICT
    )

    avoidance = AvoidanceClaim(
        claim_id=claim_id("avoidance", "science"),
        value="science",
        confidence=0.9,
        fresh_at=NOW,
        evidence_ids=(evidence().evidence_id,),
    )
    contradictory = CanonicalProfile(
        profile_id="default", revision=1, updated_at=NOW, claims=(avoidance,)
    )
    assert (
        ProposalPolicy().decide(contradictory, proposal(interest()), now=NOW).reason
        is DecisionReason.CONTRADICTION
    )


@pytest.mark.parametrize(
    ("candidate", "owner"),
    [
        (
            EmergingInterestClaim(
                claim_id=claim_id("emerging_interest", "rust"),
                value="rust",
                confidence=0.8,
                fresh_at=NOW,
                evidence_ids=(evidence().evidence_id,),
            ),
            ProposalOwner.TOPIC_LIFECYCLE,
        ),
        (
            AvoidanceClaim(
                claim_id=claim_id("avoidance", "spoilers"),
                value="spoilers",
                confidence=0.8,
                fresh_at=NOW,
                evidence_ids=(evidence().evidence_id,),
            ),
            ProposalOwner.AVOIDANCE,
        ),
        (
            InsightClaim(
                claim_id=claim_id("insight", "Likes depth"),
                value="Likes depth",
                confidence=0.8,
                fresh_at=NOW,
                evidence_ids=(evidence().evidence_id,),
            ),
            ProposalOwner.INSIGHT,
        ),
    ],
)
def test_policy_accepts_each_owned_claim_variant(
    candidate: EmergingInterestClaim | AvoidanceClaim | InsightClaim,
    owner: ProposalOwner,
) -> None:
    result = ProposalPolicy().decide(
        CanonicalProfile.empty("default", NOW),
        proposal(candidate, owner=owner),
        now=NOW,
    )
    assert result.accepted


def test_policy_rejects_non_datetime_clock() -> None:
    with pytest.raises(TypeError, match="datetime"):
        ProposalPolicy().decide(
            CanonicalProfile.empty("default", NOW), proposal(interest()), now="bad"
        )


def test_policy_accepts_and_supersedes_same_claim() -> None:
    policy = ProposalPolicy()
    empty = CanonicalProfile.empty("default", NOW)
    accepted = policy.decide(empty, proposal(interest()), now=NOW)
    assert accepted.accepted and accepted.reason is DecisionReason.ACCEPTED
    existing = CanonicalProfile(
        profile_id="default", revision=1, updated_at=NOW, claims=(interest(confidence=0.6),)
    )
    superseded = policy.decide(existing, proposal(interest(confidence=0.9)), now=NOW)
    assert superseded.accepted
    assert superseded.reason is DecisionReason.SUPERSEDED
    assert superseded.superseded_claim_id == interest().claim_id


def test_override_operation_value_invariants() -> None:
    with pytest.raises(ValidationError, match="requires a value"):
        UserOverride(
            override_id="override_" + "b" * 32,
            claim_id=interest().claim_id,
            operation=OverrideOperation.SET,
            value=None,
            created_at=NOW,
        )
    with pytest.raises(ValidationError, match="cannot carry"):
        UserOverride(
            override_id="override_" + "c" * 32,
            claim_id=interest().claim_id,
            operation=OverrideOperation.REMOVE,
            value="unexpected",
            created_at=NOW,
        )


def test_user_override_wins_and_has_audit_identity() -> None:
    profile = CanonicalProfile(
        profile_id="default", revision=1, updated_at=NOW, claims=(interest(),)
    )
    override = UserOverride.create(
        claim_id=interest().claim_id,
        operation=OverrideOperation.REMOVE,
        value=None,
        created_at=NOW,
    )
    updated = override.apply(profile)
    assert updated.claims == ()
    assert updated.overrides == (override,)
    assert updated.revision == 2
    assert override.override_id.startswith("override_")
    assert updated.lifecycle_for(interest().claim_id) is ClaimLifecycle.RETIRED


def test_override_removed_claim_still_blocks_cross_kind_inference() -> None:
    # User removed avoidance "science" via override; an inferred stable
    # interest for the same value must not silently reintroduce it.
    override = UserOverride(
        override_id="override_" + "b" * 32,
        claim_id=claim_id("avoidance", "science"),
        operation=OverrideOperation.SET,
        value="science",
        created_at=NOW,
    )
    profile = CanonicalProfile(
        profile_id="default", revision=1, updated_at=NOW, overrides=(override,)
    )
    result = ProposalPolicy().decide(
        profile,
        proposal(interest("science"), owner=ProposalOwner.PREFERENCE, links=(evidence(),)),
        now=NOW,
    )
    assert not result.accepted
    assert result.reason is DecisionReason.CONTRADICTION
