"""Typed host dependencies, security policy, and FastAPI injection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Protocol

from pydantic import ConfigDict, Field, field_validator, model_validator

from openbiliclaw.access.forms import ConnectionForm
from openbiliclaw.access.models import AccessStatus
from openbiliclaw.application.content_actions import (
    ConfirmContentActionCommand,
    PendingAction,
    ProposeContentActionCommand,
)
from openbiliclaw.application.edit_profile import EditProfileCommand, EditProfileResult
from openbiliclaw.application.reads import (
    ContentDetailsResult,
    JobHealthResult,
    ProfileResult,
    RecommendationsResult,
    SearchContentResult,
    SourcesResult,
    SourceStatusResult,
)
from openbiliclaw.application.record_feedback import RecordFeedbackCommand, RecordFeedbackResult
from openbiliclaw.application.record_observation import RecordObservationsCommand
from openbiliclaw.application.refresh_recommendations import (
    RefreshRecommendationsCommand,
    RefreshRecommendationsResult,
)
from openbiliclaw.application.sources import (
    ConnectSourceCommand,
    ConnectSourceResult,
    DisconnectSourceCommand,
)
from openbiliclaw.assistant.models import AssistantOutput, Conversation, ConversationMessage
from openbiliclaw.content.integration.actions import ActionResult
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.observations.service import RecordBatchResult

from .model_configuration import ModelConfiguration
from .schemas.models import EventEnvelope


class HostSecurityPolicy(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    bind_host: str = "127.0.0.1"
    bearer_token: str | None = Field(default=None, repr=False, exclude=True)
    allowed_origins: tuple[str, ...] = ("http://localhost:8420", "http://127.0.0.1:8420")
    allowed_origin_schemes: tuple[str, ...] = ("chrome-extension://", "moz-extension://")
    max_body_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    request_timeout_seconds: float = Field(default=30, gt=0, le=120)
    requests_per_minute: int = Field(default=120, ge=1, le=10_000)
    max_websocket_subscribers: int = Field(default=16, ge=1, le=1000)
    replay_limit: int = Field(default=100, ge=1, le=1000)
    reconnect_milliseconds: int = Field(default=3000, ge=100, le=60_000)

    @field_validator("bind_host")
    @classmethod
    def valid_host(cls, value: str) -> str:
        try:
            ip_address(value)
        except ValueError:
            if value != "localhost":
                raise ValueError("bind_host must be an IP address or localhost") from None
        return value

    def origin_allowed(self, origin: str) -> bool:
        return origin in self.allowed_origins or origin.startswith(self.allowed_origin_schemes)

    @model_validator(mode="after")
    def auth_required_off_loopback(self) -> HostSecurityPolicy:
        if (
            self.bind_host != "localhost"
            and not ip_address(self.bind_host).is_loopback
            and not self.bearer_token
        ):
            raise ValueError("bearer_token is required for non-loopback binding")
        return self


class DiagnosticResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    healthy: bool
    detail: str = Field(max_length=500)


class StartResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    started: bool


class HostFacade(Protocol):
    async def source_status(
        self, provider_id: str, account_id: str | None
    ) -> SourceStatusResult: ...
    async def source_form(self, provider_id: str, method_id: str) -> ConnectionForm: ...
    async def list_sources(self, account_id: str | None, limit: int) -> SourcesResult: ...
    def provider_capabilities(self, provider_id: str) -> tuple[str, ...]: ...
    async def connect_source(self, command: ConnectSourceCommand) -> ConnectSourceResult: ...
    async def disconnect_source(self, command: DisconnectSourceCommand) -> AccessStatus: ...
    async def get_recommendations(self, limit: int) -> RecommendationsResult: ...
    async def refresh_recommendations(
        self, command: RefreshRecommendationsCommand
    ) -> RefreshRecommendationsResult: ...
    async def record_feedback(self, command: RecordFeedbackCommand) -> RecordFeedbackResult: ...
    async def record_observations(
        self, command: RecordObservationsCommand
    ) -> RecordBatchResult: ...
    async def show_profile(self, profile_id: str) -> ProfileResult: ...
    async def edit_profile(self, command: EditProfileCommand) -> EditProfileResult: ...
    async def search_content(
        self, provider_id: str, text: str, limit: int
    ) -> SearchContentResult: ...
    async def get_content_details(self, reference: str) -> ContentDetailsResult: ...
    async def propose_action(self, command: ProposeContentActionCommand) -> PendingAction: ...
    async def confirm_action(self, command: ConfirmContentActionCommand) -> ActionResult: ...
    async def assistant_turn(
        self, request: AssistantTurnInput, device_id: str
    ) -> AssistantOutput: ...
    async def conversation(self, conversation_id: str, device_id: str) -> Conversation: ...
    async def conversation_messages(
        self, conversation_id: str, device_id: str, limit: int
    ) -> tuple[ConversationMessage, ...]: ...
    async def job_health(self) -> JobHealthResult: ...
    async def config_diagnostics(self) -> DiagnosticResult: ...
    async def model_diagnostics(self) -> DiagnosticResult: ...
    async def start(self) -> StartResult: ...


class AssistantTurnInput(Protocol):
    conversation_id: str
    text: str
    locale: str


class EventSource(Protocol):
    async def replay(self, after: int, limit: int) -> tuple[EventEnvelope, ...]: ...


class HostLifespan(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@dataclass(slots=True)
class HostDependencies:
    facade: HostFacade
    security: HostSecurityPolicy = HostSecurityPolicy()
    events: EventSource | None = None
    lifespan: HostLifespan | None = None
    models: ModelConfiguration | None = None
    websocket_slots: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.websocket_slots = asyncio.Semaphore(self.security.max_websocket_subscribers)


def get_dependencies() -> HostDependencies:
    """Overridden by the app factory; never reads dynamic request.state."""
    raise RuntimeError("host dependencies were not configured")
