"""Atomic recommendation feedback + observation workflow."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import ConfigDict, Field

from openbiliclaw.content.integration.identity import (
    ContentRef,  # noqa: TC001  # Runtime type required by Pydantic model fields.
)
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.observations.models import (
    EmptyPayload,
    Observation,
    OpenedPayload,
    ReasonPayload,
    RecommendationDislikedObservation,
    RecommendationDismissedObservation,
    RecommendationLikedObservation,
    RecommendationOpenedObservation,
    RecommendationSavedObservation,
)
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.recommendation.models import FeedbackKind, FeedbackRecord, record_identity

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


class FeedbackObservationUnitOfWork(Protocol):
    async def record_feedback(self, feedback: FeedbackRecord, observation: Observation) -> bool: ...


class RecordFeedbackCommand(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: str = Field(min_length=8, max_length=200)
    shown_id: str = Field(min_length=1, max_length=128)
    content_ref: ContentRef
    kind: FeedbackKind
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=500)
    dwell_ms: int | None = Field(default=None, ge=0, le=86_400_000)


class RecordFeedbackResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    feedback_id: str
    observation_id: str
    inserted: bool


@dataclass(frozen=True, slots=True)
class RecordFeedback:
    unit_of_work: FeedbackObservationUnitOfWork
    clock: Callable[[], datetime]

    async def __call__(self, command: RecordFeedbackCommand) -> RecordFeedbackResult:
        now = self.clock()
        feedback_id = record_identity("feedback", command.idempotency_key)
        observation_id = "obs_" + hashlib.sha256(command.idempotency_key.encode()).hexdigest()[:32]
        feedback = FeedbackRecord(
            feedback_id=feedback_id,
            shown_id=command.shown_id,
            kind=command.kind,
            occurred_at=now,
        )
        common = {
            "observation_id": observation_id,
            "idempotency_key": command.idempotency_key,
            "occurred_at": now,
            "received_at": now,
            "account_id": command.account_id,
            "content_ref": command.content_ref,
            "provenance": ObservationProvenance(
                producer_id="application.feedback",
                source=ObservationSource.RECOMMENDATION,
                authenticated=command.account_id is not None,
                trust_level=TrustLevel.HIGH if command.account_id is not None else TrustLevel.LOW,
            ),
        }
        observation: Observation
        if command.kind is FeedbackKind.OPENED:
            observation = RecommendationOpenedObservation(
                **common, payload=OpenedPayload(dwell_ms=command.dwell_ms)
            )
        elif command.kind is FeedbackKind.LIKED:
            observation = RecommendationLikedObservation(**common, payload=EmptyPayload())
        elif command.kind is FeedbackKind.DISLIKED:
            observation = RecommendationDislikedObservation(
                **common, payload=ReasonPayload(reason=command.reason)
            )
        elif command.kind is FeedbackKind.SAVED:
            observation = RecommendationSavedObservation(**common, payload=EmptyPayload())
        else:
            observation = RecommendationDismissedObservation(
                **common, payload=ReasonPayload(reason=command.reason)
            )
        inserted = await self.unit_of_work.record_feedback(feedback, observation)
        return RecordFeedbackResult(
            feedback_id=feedback_id, observation_id=observation_id, inserted=inserted
        )
