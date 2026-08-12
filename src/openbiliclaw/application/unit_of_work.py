"""SQLite transaction adapters for cross-module application workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.understanding.ledger import LedgerEntry, LedgerStatus
from openbiliclaw.understanding.overrides import OverrideOperation, UserOverride
from openbiliclaw.understanding.repository import ledger_identity

if TYPE_CHECKING:
    from openbiliclaw.observations.models import DeterministicProfileEditObservation, Observation
    from openbiliclaw.observations.repository import SqliteObservationRepository
    from openbiliclaw.recommendation.models import FeedbackRecord
    from openbiliclaw.recommendation.repositories import SqliteRecommendationRepository
    from openbiliclaw.understanding.profile import CanonicalProfile
    from openbiliclaw.understanding.repository import SqliteUnderstandingRepository


class FeedbackUnitOfWork:
    """Commit feedback and its learning observation as one logical operation."""

    def __init__(
        self,
        recommendations: SqliteRecommendationRepository,
        observations: SqliteObservationRepository,
    ) -> None:
        self._recommendations = recommendations
        self._observations = observations

    async def record_feedback(self, feedback: FeedbackRecord, observation: Observation) -> bool:
        # Both repositories share the single serialized database. Insert the
        # immutable observation first so a retry cannot lose learning evidence.
        await self._observations.insert_batch((observation,))
        return await self._recommendations.save_feedback(feedback)


class ProfileEditUnitOfWork:
    """Apply a deterministic profile override and append its audit observation."""

    def __init__(
        self,
        understanding: SqliteUnderstandingRepository,
        observations: SqliteObservationRepository,
    ) -> None:
        self._understanding = understanding
        self._observations = observations

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
        return updated
