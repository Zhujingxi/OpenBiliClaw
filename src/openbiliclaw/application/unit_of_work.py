"""SQLite transaction adapters for cross-module application workflows."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.understanding.ledger import LedgerEntry, LedgerStatus
from openbiliclaw.understanding.overrides import OverrideOperation, UserOverride
from openbiliclaw.understanding.repository import ledger_identity
from openbiliclaw.understanding.resynthesis import ResynthesisTrigger

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
    ) -> None:
        self._understanding = understanding
        self._observations = observations
        self._resynthesis = resynthesis

    async def edit_profile(
        self,
        profile_id: str,
        *,
        claim_id: str,
        operation: OverrideOperation,
        value: str | None,
        observation: DeterministicProfileEditObservation,
    ) -> CanonicalProfile:
        profile = await self._understanding.load_profile(profile_id, now=observation.occurred_at)
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
