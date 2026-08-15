"""Atomic recommendation feedback + observation workflow."""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import ConfigDict, Field

from openbiliclaw.content.integration.identity import (
    ContentRef,  # noqa: TC001  # Runtime type required by Pydantic model fields.
)
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.observations.models import (
    Observation,
    OpenedPayload,
    ReasonPayload,
    RecommendationDislikedObservation,
    RecommendationDismissedObservation,
    RecommendationFeedbackPayload,
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

from .errors import ApplicationError, ApplicationErrorCode

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from openbiliclaw.recommendation.models import ExplorationAttribution


class FeedbackTargetReads(Protocol):
    async def content_ref_for_shown(self, shown_id: str) -> ContentRef: ...
    async def exploration_for_shown(self, shown_id: str) -> ExplorationAttribution | None: ...


class FeedbackObservationUnitOfWork(Protocol):
    async def record_feedback(
        self, feedback: FeedbackRecord, observation: Observation, content_ref: ContentRef
    ) -> bool: ...


class FeedbackRewardSink(Protocol):
    async def __call__(self, feedback: FeedbackRecord, observation: Observation) -> None: ...


class RecordFeedbackCommand(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    idempotency_key: str = Field(min_length=8, max_length=200)
    shown_id: str = Field(min_length=1, max_length=128)
    content_ref: ContentRef
    kind: FeedbackKind
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=500)
    dwell_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    exposed: bool = False


class RecordFeedbackResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    feedback_id: str
    observation_id: str
    inserted: bool


@dataclass(frozen=True, slots=True)
class RecordFeedbackForShown:
    """Resolve a delivered impression then delegate to the canonical feedback workflow."""

    targets: FeedbackTargetReads
    record: RecordFeedback

    async def __call__(
        self, *, shown_id: str, kind: FeedbackKind, idempotency_key: str, exposed: bool = False
    ) -> RecordFeedbackResult:
        try:
            content_ref = await self.targets.content_ref_for_shown(shown_id)
        except KeyError as exc:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "shown record not found"
            ) from exc
        return await self.record(
            RecordFeedbackCommand(
                idempotency_key=idempotency_key,
                shown_id=shown_id,
                content_ref=content_ref,
                kind=kind,
                exposed=exposed,
            )
        )


@dataclass(frozen=True, slots=True)
class RecordFeedback:
    unit_of_work: FeedbackObservationUnitOfWork
    clock: Callable[[], datetime]
    targets: FeedbackTargetReads | None = None
    reward_sink: FeedbackRewardSink | None = None

    async def __call__(self, command: RecordFeedbackCommand) -> RecordFeedbackResult:
        now = self.clock()
        feedback_id = record_identity("feedback", command.idempotency_key)
        observation_id = "obs_" + hashlib.sha256(command.idempotency_key.encode()).hexdigest()[:32]
        attribution = None
        if self.targets is not None:
            try:
                attribution = await self.targets.exploration_for_shown(command.shown_id)
            except KeyError as exc:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "shown recommendation not found"
                ) from exc
        feedback = FeedbackRecord(
            feedback_id=feedback_id,
            shown_id=command.shown_id,
            kind=command.kind,
            occurred_at=now,
            exposed=command.exposed,
        )
        payload_common = {
            "exploration_arm": attribution.arm if attribution is not None else None,
            "exploration_hypothesis_id": (
                attribution.hypothesis_id if attribution is not None else None
            ),
            "exposed": command.exposed,
        }
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
                **common, payload=OpenedPayload(**payload_common, dwell_ms=command.dwell_ms)
            )
        elif command.kind is FeedbackKind.LIKED:
            observation = RecommendationLikedObservation(
                **common, payload=RecommendationFeedbackPayload(**payload_common)
            )
        elif command.kind is FeedbackKind.DISLIKED:
            observation = RecommendationDislikedObservation(
                **common, payload=ReasonPayload(**payload_common, reason=command.reason)
            )
        elif command.kind is FeedbackKind.SAVED:
            observation = RecommendationSavedObservation(
                **common, payload=RecommendationFeedbackPayload(**payload_common)
            )
        else:
            observation = RecommendationDismissedObservation(
                **common, payload=ReasonPayload(**payload_common, reason=command.reason)
            )
        try:
            inserted = await self.unit_of_work.record_feedback(
                feedback, observation, command.content_ref
            )
        except KeyError as error:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "shown recommendation not found"
            ) from error
        except ValueError as error:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, str(error)) from error
        if inserted and self.reward_sink is not None:
            # The feedback/observation UoW has committed; reward credit is the
            # learning plane and must never fail an accepted feedback response
            # (e.g. late feedback on a killed hypothesis raises ValueError there).
            with contextlib.suppress(Exception):
                await self.reward_sink(feedback, observation)
        return RecordFeedbackResult(
            feedback_id=feedback_id, observation_id=observation_id, inserted=inserted
        )
