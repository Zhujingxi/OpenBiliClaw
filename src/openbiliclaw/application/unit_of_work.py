"""SQLite transaction adapters for cross-module application workflows."""

from __future__ import annotations

import hashlib
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.understanding.evidence import EvidenceLink
from openbiliclaw.understanding.ledger import LedgerEntry, LedgerStatus
from openbiliclaw.understanding.overrides import OverrideOperation, UserOverride
from openbiliclaw.understanding.policy import ProposalPolicy
from openbiliclaw.understanding.profile import (
    AvoidanceClaim,
    EmergingInterestClaim,
    InsightClaim,
    PreferenceClaim,
    ProfileClaim,
    StableInterestClaim,
    claim_id,
)
from openbiliclaw.understanding.proposals import ClaimProposal, ProposalOwner
from openbiliclaw.understanding.repository import ledger_identity
from openbiliclaw.understanding.resynthesis import ResynthesisTrigger
from openbiliclaw.understanding.service import (
    EmbeddingWriter,
    apply_proposals,
    index_understanding_commit,
)

if TYPE_CHECKING:
    from openbiliclaw.content.integration.identity import ContentRef
    from openbiliclaw.observations.models import DeterministicProfileEditObservation, Observation
    from openbiliclaw.observations.repository import SqliteObservationRepository
    from openbiliclaw.recommendation.models import FeedbackRecord
    from openbiliclaw.recommendation.repositories import SqliteRecommendationRepository
    from openbiliclaw.understanding.profile import CanonicalProfile
    from openbiliclaw.understanding.repository import SqliteUnderstandingRepository
    from openbiliclaw.understanding.resynthesis import ResynthesisResult


class ProfileResynthesis(Protocol):
    async def resynthesize(
        self,
        profile_id: str,
        trigger: ResynthesisTrigger,
        claim_ids: tuple[str, ...],
    ) -> ResynthesisResult: ...


class FeedbackUnitOfWork:
    """Commit feedback and its learning observation as one logical operation."""

    def __init__(
        self,
        recommendations: SqliteRecommendationRepository,
        observations: SqliteObservationRepository,
    ) -> None:
        self._recommendations = recommendations
        self._observations = observations

    async def record_feedback(
        self, feedback: FeedbackRecord, observation: Observation, content_ref: ContentRef
    ) -> bool:
        # Both repositories share one database; the delivery transition, feedback,
        # and learning evidence therefore commit or roll back together.
        async with self._recommendations.db.transaction() as session:
            inserted = await self._recommendations.save_feedback_session(
                session, feedback, content_ref
            )
            if inserted:
                await self._observations.insert_batch_session(session, (observation,))
            return inserted


class ProfileEditUnitOfWork:
    """Apply a deterministic profile override and append its audit observation."""

    def __init__(
        self,
        understanding: SqliteUnderstandingRepository,
        observations: SqliteObservationRepository,
        *,
        resynthesis: ProfileResynthesis | None = None,
        embedding_index: EmbeddingWriter | None = None,
    ) -> None:
        self._understanding = understanding
        self._observations = observations
        self._resynthesis = resynthesis
        self._embedding_index = embedding_index

    async def edit_profile(
        self,
        profile_id: str,
        *,
        claim_id: str,
        operation: OverrideOperation,
        value: str | None,
        observation: DeterministicProfileEditObservation,
    ) -> CanonicalProfile:
        evidence_id = "ev_" + observation.observation_id.removeprefix("obs_")
        profile = await self._understanding.load_profile(profile_id, now=observation.occurred_at)
        if await self._understanding.evidence_exists(evidence_id):
            return profile
        original = next((item for item in profile.claims if item.claim_id == claim_id), None)
        override = UserOverride.create(
            claim_id=claim_id,
            operation=operation,
            value=value,
            created_at=observation.occurred_at,
        )
        updated = override.apply(profile)
        await self._observations.insert_batch((observation,))
        await self._understanding.commit_override(
            updated,
            LedgerEntry(
                ledger_id=ledger_identity(override.override_id, LedgerStatus.OVERRIDE.value),
                profile_id=profile_id,
                override_id=override.override_id,
                claim_id=claim_id,
                status=LedgerStatus.OVERRIDE,
                reason=f"user_{operation.value}",
                decided_at=observation.occurred_at,
            ),
        )
        evidence = _statement_evidence(observation)
        proposals: tuple[ClaimProposal, ...] = ()
        decisions: tuple[LedgerEntry, ...] = ()
        if operation is OverrideOperation.SET and original is not None and value is not None:
            replacement = _statement_claim(original, value, evidence)
            proposal = ClaimProposal(
                proposal_id="prop_"
                + hashlib.sha256(
                    f"{observation.observation_id}:{replacement.claim_id}".encode()
                ).hexdigest()[:32],
                analyzer_id="understanding.user_statement.v1",
                owner=_statement_owner(replacement),
                claim=replacement,
                evidence=(evidence,),
                proposed_at=observation.occurred_at,
            )
            updated, decisions, _accepted, _rejected = apply_proposals(
                updated, (proposal,), now=observation.occurred_at, policy=ProposalPolicy()
            )
            proposals = (proposal,)
        await self._understanding.commit_analysis(
            profile=updated,
            proposals=proposals,
            decisions=decisions,
            evidence=(evidence,),
            analyzer_id="understanding.user_statement.v1",
            checkpoint="0",
        )
        await index_understanding_commit(self._embedding_index, updated, proposals, (evidence,))
        if self._resynthesis is None:
            return updated
        # The override and its observation are already durable; a best-effort
        # follow-up must not report the committed edit as failed.
        with suppress(Exception):
            result = await self._resynthesis.resynthesize(
                profile_id, ResynthesisTrigger.EXPLICIT_CORRECTION, (claim_id,)
            )
            return result.profile
        return updated


def _statement_evidence(observation: DeterministicProfileEditObservation) -> EvidenceLink:
    payload = observation.payload
    detail = f"User statement: {payload.operation} {payload.field}"
    if payload.value is not None:
        detail += f" to {payload.value}"
    return EvidenceLink(
        evidence_id="ev_" + observation.observation_id.removeprefix("obs_"),
        observation_id=observation.observation_id,
        summary=detail[:500],
        occurred_at=observation.occurred_at,
        trust=1.0,
    )


def _statement_claim(original: ProfileClaim, value: str, evidence: EvidenceLink) -> ProfileClaim:
    identity_value = (
        f"{original.dimension.value}:{value}" if isinstance(original, PreferenceClaim) else value
    )
    return original.model_copy(
        update={
            "claim_id": claim_id(original.kind, identity_value),
            "value": value,
            "confidence": 1.0,
            "fresh_at": evidence.occurred_at,
            "evidence_ids": (evidence.evidence_id,),
        }
    )


def _statement_owner(claim: ProfileClaim) -> ProposalOwner:
    if isinstance(claim, (StableInterestClaim, PreferenceClaim)):
        return ProposalOwner.PREFERENCE
    if isinstance(claim, AvoidanceClaim):
        return ProposalOwner.AVOIDANCE
    if isinstance(claim, EmergingInterestClaim):
        return ProposalOwner.TOPIC_LIFECYCLE
    if isinstance(claim, InsightClaim):
        return ProposalOwner.INSIGHT
    raise TypeError("unsupported statement claim")
