"""Strict transport-only schemas mirroring application and assistant DTOs."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import ConfigDict, Field, SecretStr

from openbiliclaw.access.forms import ConnectionForm
from openbiliclaw.access.models import AccessStatus, Permission
from openbiliclaw.application.content_actions import (
    ConfirmContentActionCommand,
    PendingAction,
    ProposeContentActionCommand,
)
from openbiliclaw.application.edit_profile import EditProfileCommand
from openbiliclaw.application.record_feedback import RecordFeedbackCommand, RecordFeedbackResult
from openbiliclaw.application.record_observation import RecordObservationsCommand
from openbiliclaw.application.refresh_recommendations import RefreshRecommendationsCommand
from openbiliclaw.assistant.models import (
    AssistantClarification,
    AssistantMessage,
    AssistantPendingAction,
    AssistantRecommendationPresentation,
    Conversation,
    ConversationMessage,
)
from openbiliclaw.content.integration.actions import ActionResult
from openbiliclaw.content.integration.identity import ContentRef
from openbiliclaw.content.integration.native import NativeContent
from openbiliclaw.content.integration.projections import CardData, ContentPreview
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.core.health import HealthSnapshot
from openbiliclaw.observations.models import Observation
from openbiliclaw.observations.service import RecordBatchResult
from openbiliclaw.recommendation.models import FeedbackKind, RecommendationFeedItem
from openbiliclaw.understanding.overrides import OverrideOperation
from openbiliclaw.understanding.projections import DialogueProfile


class TransportModel(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ErrorCode(StrEnum):
    VALIDATION = "validation"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    UNAVAILABLE = "unavailable_capability"
    CONFLICT = "conflict"
    RATE_LIMIT = "rate_limit"
    TEMPORARY_FAILURE = "temporary_failure"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"


class ErrorDetail(TransportModel):
    code: ErrorCode
    message: str = Field(max_length=500)


class ErrorEnvelope(TransportModel):
    error: ErrorDetail


class SourceListResponse(TransportModel):
    items: tuple[AccessStatus, ...]


class SourceFormResponse(TransportModel):
    form: ConnectionForm


class ConnectSourceRequest(TransportModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, json_schema_extra={"examples": []}
    )
    provider_id: str = Field(min_length=1, max_length=128)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    method_id: str = Field(min_length=1, max_length=128)
    permissions: frozenset[Permission] = frozenset({Permission.READ_PUBLIC})
    idempotency_key: str = Field(min_length=8, max_length=200)
    credential: SecretStr | None = Field(default=None, repr=False, exclude=True)


class DisconnectSourceRequest(TransportModel):
    provider_id: str = Field(min_length=1, max_length=128)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=200)


class SourceMutationResponse(TransportModel):
    status: AccessStatus
    availability_refreshed: bool = True
    recoverable: bool = False


class RecommendationPage(TransportModel):
    items: tuple[RecommendationFeedItem, ...]


class RefreshResponse(TransportModel):
    decision: str


class ProfileResponse(TransportModel):
    profile: DialogueProfile


class SearchResponse(TransportModel):
    items: tuple[ContentPreview, ...]


class CardDataResponse(TransportModel):
    """Canonical provider presentation DTO exposed for generated clients."""

    card: CardData


class ContentResponse(TransportModel):
    content: NativeContent


AssistantOutput: TypeAlias = Annotated[
    AssistantMessage
    | AssistantRecommendationPresentation
    | AssistantClarification
    | AssistantPendingAction,
    Field(discriminator="kind"),
]


class AssistantTurnRequest(TransportModel):
    conversation_id: str = Field(pattern=r"^conv_[0-9a-f]{32}$")
    text: str = Field(min_length=1, max_length=8000)
    locale: str = Field(default="en-US", min_length=2, max_length=32)


class AssistantTurnResponse(TransportModel):
    output: AssistantOutput


class ConversationResponse(TransportModel):
    conversation: Conversation
    messages: tuple[ConversationMessage, ...] = ()


class RuntimeResponse(TransportModel):
    health: HealthSnapshot


class FeedbackResponse(TransportModel):
    result: RecordFeedbackResult


class ObservationsResponse(TransportModel):
    result: RecordBatchResult


class PendingActionResponse(TransportModel):
    action: PendingAction


class ActionResultResponse(TransportModel):
    result: ActionResult


class EventKind(StrEnum):
    JOB = "job"
    RECOMMENDATION = "recommendation"
    ASSISTANT = "assistant"
    CONNECTION = "connection"


class JobEvent(TransportModel):
    kind: Literal[EventKind.JOB] = EventKind.JOB
    event_id: int = Field(ge=1)
    component_id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)


class RecommendationEvent(TransportModel):
    kind: Literal[EventKind.RECOMMENDATION] = EventKind.RECOMMENDATION
    event_id: int = Field(ge=1)
    recommendation_id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)


class AssistantEvent(TransportModel):
    kind: Literal[EventKind.ASSISTANT] = EventKind.ASSISTANT
    event_id: int = Field(ge=1)
    conversation_id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)


class ConnectionEvent(TransportModel):
    kind: Literal[EventKind.CONNECTION] = EventKind.CONNECTION
    event_id: int = Field(ge=1)
    provider_id: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=64)


EventEnvelope: TypeAlias = Annotated[
    JobEvent | RecommendationEvent | AssistantEvent | ConnectionEvent,
    Field(discriminator="kind"),
]


class FeedbackRequest(TransportModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    shown_id: str = Field(min_length=1, max_length=128)
    content_ref: ContentRef
    kind: Literal["opened", "liked", "disliked", "saved", "dismissed"]
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=500)
    dwell_ms: int | None = Field(default=None, ge=0, le=86_400_000)

    def to_command(self) -> RecordFeedbackCommand:
        data = self.model_dump()
        data["kind"] = FeedbackKind(self.kind)
        return RecordFeedbackCommand.model_validate(data)


class ObservationsRequest(TransportModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    observations: list[Observation] = Field(min_length=1, max_length=100)
    allowed_event_types: list[str] = Field(min_length=1)

    def to_command(self) -> RecordObservationsCommand:
        return RecordObservationsCommand(
            idempotency_key=self.idempotency_key,
            observations=tuple(self.observations),
            allowed_event_types=frozenset(self.allowed_event_types),
        )


class ProfileEditRequest(TransportModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    profile_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=128)
    claim_id: str = Field(pattern=r"^claim_[0-9a-f]{32}$")
    operation: Literal["set", "remove"]
    value: str | None = Field(default=None, max_length=500)

    def to_command(self) -> EditProfileCommand:
        data = self.model_dump()
        data["operation"] = OverrideOperation(self.operation)
        return EditProfileCommand.model_validate(data)


class ProposeActionRequest(TransportModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    action_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    ref: ContentRef
    user_id: str = Field(min_length=1, max_length=128)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    safe_preview: str = Field(min_length=1, max_length=500)
    expires_in_seconds: int = Field(default=300, ge=1, le=3600)

    def to_command(self) -> ProposeContentActionCommand:
        return ProposeContentActionCommand.model_validate(self.model_dump())


class ConfirmActionRequest(TransportModel):
    pending_action_id: str = Field(pattern=r"^pending_[0-9a-f]{32}$")
    user_id: str = Field(min_length=1, max_length=128)

    def to_command(self) -> ConfirmContentActionCommand:
        return ConfirmContentActionCommand.model_validate(self.model_dump())


class RefreshRequest(TransportModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    maximum_items: int = Field(default=50, ge=1, le=100)

    def to_command(self) -> RefreshRecommendationsCommand:
        return RefreshRecommendationsCommand(
            idempotency_key=self.idempotency_key, maximum_items=self.maximum_items
        )
