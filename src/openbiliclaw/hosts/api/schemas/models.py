"""Strict transport-only schemas mirroring application and assistant DTOs."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import ConfigDict, Field, JsonValue, SecretStr, field_validator, model_validator

from openbiliclaw.access.forms import ConnectionForm
from openbiliclaw.access.models import AccessStatus, Permission, VerificationResult
from openbiliclaw.ai.providers.catalog import Protocol
from openbiliclaw.application.content_actions import (
    ConfirmContentActionCommand,
    PendingAction,
    PendingActionResult,
    ProposeContentActionCommand,
    RejectPendingActionCommand,
)
from openbiliclaw.application.edit_profile import (
    EXPLORATION_DISABLED_CLAIM_ID,
    EditProfileCommand,
)
from openbiliclaw.application.plugin_access import SubmittedAccessArtifact
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
from openbiliclaw.content.integration.identity import ContentRef
from openbiliclaw.content.integration.manifest import AccessRecipe
from openbiliclaw.content.integration.projections import CardData, ContentPreview
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.core.config import CapabilitySettings
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


class SourceStatusEntry(TransportModel):
    """Flat source status plus the provider's declared capability kinds."""

    provider_id: str = Field(min_length=1, max_length=128)
    account_id: str | None = None
    state: str = Field(min_length=1, max_length=32)
    method_id: str | None = None
    verification: VerificationResult | None = None
    capabilities: tuple[str, ...] = ()


class SourceListResponse(TransportModel):
    items: tuple[SourceStatusEntry, ...]


class SourceFormResponse(TransportModel):
    form: ConnectionForm


class AccessRecipeResponse(TransportModel):
    recipe: AccessRecipe


class AccessMaterialRequest(TransportModel):
    artifacts: tuple[SubmittedAccessArtifact, ...] = Field(min_length=1, max_length=64)

    @field_validator("artifacts", mode="before")
    @classmethod
    def _coerce_artifacts(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class ConnectSourceRequest(TransportModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, json_schema_extra={"examples": []}
    )
    provider_id: str = Field(min_length=1, max_length=128)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    method_id: str = Field(min_length=1, max_length=128)
    permissions: frozenset[Permission] = frozenset({Permission.READ_PUBLIC})
    idempotency_key: str = Field(min_length=8, max_length=200)
    submission: dict[str, SecretStr] | None = Field(default=None, repr=False, exclude=True)

    @field_validator("permissions", mode="before")
    @classmethod
    def _coerce_permissions(cls, value: object) -> object:
        """Accept JSON arrays for the strict frozenset field."""

        if isinstance(value, list | set | tuple):
            return frozenset(Permission(item) for item in value)
        return value


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


class NativeContentView(TransportModel):
    """Transport shape of NativeContent: payload serialized as a plain JSON object."""

    ref: ContentRef
    schema_version: int = Field(ge=1)
    payload: dict[str, JsonValue]


class ContentResponse(TransportModel):
    content: NativeContentView


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


class CapabilityResponse(TransportModel):
    tools: bool
    structured_output: bool
    vision: bool
    context_tokens: int = Field(ge=0)
    streaming: bool
    reasoning: bool


class CatalogModelResponse(TransportModel):
    id: str
    name: str
    reasoning: bool
    tool_call: bool
    structured_output: bool
    context_limit: int = Field(ge=0)


class CatalogProviderResponse(TransportModel):
    id: str
    name: str
    env: tuple[str, ...]
    protocol: Protocol
    models: tuple[CatalogModelResponse, ...]


class ModelCatalogResponse(TransportModel):
    providers: tuple[CatalogProviderResponse, ...]


class ModelConfigurationView(TransportModel):
    provider: str
    model_name: str
    endpoint: str | None
    secret_configured: bool
    protocol: Protocol | None
    capabilities: CapabilityResponse | None


class EmbeddingConfigurationResponse(TransportModel):
    provider: str
    model_name: str
    endpoint: str | None
    secret_configured: bool


class CurrentModelResponse(TransportModel):
    model: ModelConfigurationView
    embedding: EmbeddingConfigurationResponse


class ModelConfigurationResponse(TransportModel):
    current: CurrentModelResponse
    reloaded: bool
    restart_required: bool


class ModelConfigurationRequest(TransportModel):
    provider: str = Field(min_length=1, max_length=200)
    model_name: str = Field(min_length=1, max_length=200)
    endpoint: str | None = Field(default=None, max_length=2_000, pattern=r"^https?://")
    api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    protocol: Protocol | None = None
    capabilities: CapabilitySettings | None = None


class FeedbackResponse(TransportModel):
    result: RecordFeedbackResult


class ObservationsResponse(TransportModel):
    result: RecordBatchResult


class PendingActionResponse(TransportModel):
    action: PendingAction


class ActionResultResponse(TransportModel):
    result: PendingActionResult


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
    exposed: bool = False

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
    claim_id: str | None = Field(default=None, pattern=r"^claim_[0-9a-f]{32}$")
    field: Literal["exploration.disabled"] | None = None
    operation: Literal["set", "remove"]
    value: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def resolve_explicit_field(self) -> ProfileEditRequest:
        if self.claim_id is None and self.field is None:
            raise ValueError("claim_id or field is required")
        if self.field is not None and self.claim_id not in (
            None,
            EXPLORATION_DISABLED_CLAIM_ID,
        ):
            raise ValueError("claim_id does not match field")
        if (
            self.field == "exploration.disabled"
            and self.operation == "set"
            and self.value != "true"
        ):
            raise ValueError("exploration.disabled must be set to true")
        if self.operation == "remove" and self.value is not None:
            raise ValueError("remove operation must not carry a value")
        return self

    def to_command(self) -> EditProfileCommand:
        data = self.model_dump()
        data.pop("field")
        data["claim_id"] = self.claim_id or EXPLORATION_DISABLED_CLAIM_ID
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

    def to_reject_command(self) -> RejectPendingActionCommand:
        return RejectPendingActionCommand.model_validate(self.model_dump())


class RefreshRequest(TransportModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    maximum_items: int = Field(default=50, ge=1, le=100)

    def to_command(self) -> RefreshRecommendationsCommand:
        return RefreshRecommendationsCommand(
            idempotency_key=self.idempotency_key, maximum_items=self.maximum_items
        )
