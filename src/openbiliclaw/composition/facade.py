"""Concrete narrow host facade over target application workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.application.reads import (
    ContentDetailsResult,
    GetContentDetails,
    GetContentDetailsQuery,
    GetRecommendations,
    GetRecommendationsQuery,
    GetSourceForm,
    GetSourceFormQuery,
    GetSourceStatus,
    GetSourceStatusQuery,
    JobHealthResult,
    ListSources,
    ListSourcesQuery,
    ProfileResult,
    RecommendationsResult,
    SearchContent,
    SearchContentQuery,
    SearchContentResult,
    ShowProfile,
    ShowProfileQuery,
    SourcesResult,
    SourceStatusResult,
)
from openbiliclaw.application.record_observation import (
    RecordObservations,
    RecordObservationsCommand,
)
from openbiliclaw.application.sources import (
    ConnectSource,
    ConnectSourceCommand,
    ConnectSourceResult,
    DisconnectSource,
    DisconnectSourceCommand,
)
from openbiliclaw.content.integration.identity import ContentRef, ProviderId
from openbiliclaw.hosts.api.dependencies import DiagnosticResult, StartResult

if TYPE_CHECKING:
    from openbiliclaw.access.forms import ConnectionForm
    from openbiliclaw.access.models import AccessStatus
    from openbiliclaw.application.content_actions import (
        ConfirmContentActionCommand,
        PendingAction,
        ProposeContentActionCommand,
    )
    from openbiliclaw.application.edit_profile import EditProfileCommand, EditProfileResult
    from openbiliclaw.application.record_feedback import (
        RecordFeedbackCommand,
        RecordFeedbackResult,
    )
    from openbiliclaw.application.refresh_recommendations import (
        RefreshRecommendationsCommand,
        RefreshRecommendationsResult,
    )
    from openbiliclaw.assistant.models import (
        AssistantOutput,
        Conversation,
        ConversationMessage,
    )
    from openbiliclaw.content.integration.actions import ActionResult
    from openbiliclaw.core.config import AppSettings
    from openbiliclaw.core.health import HealthSnapshot
    from openbiliclaw.observations.service import RecordBatchResult


class _Availability:
    async def refresh(self, provider_id: str) -> None:
        del provider_id


@dataclass(slots=True)
class _Journal:
    values: dict[str, str] = field(default_factory=dict)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def put(self, key: str, value: str) -> None:
        self.values[key] = value


class HealthSource(Protocol):
    def health(self) -> HealthSnapshot: ...


class CompositionFacade:
    """Transport-complete facade; unavailable optional capabilities fail explicitly."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        access: object,
        provider_ids: tuple[str, ...],
        registry: object,
        observations: object,
        understanding: object,
        recommendations: object,
        health: HealthSource,
    ) -> None:
        from openbiliclaw.access.service import AccessService
        from openbiliclaw.content.integration.registry import ContentProviderRegistry
        from openbiliclaw.observations.service import ObservationIngressService
        from openbiliclaw.recommendation.service import RecommendationService
        from openbiliclaw.understanding.service import UnderstandingService

        if not isinstance(access, AccessService):
            raise TypeError("invalid access service")
        if not isinstance(registry, ContentProviderRegistry):
            raise TypeError("invalid provider registry")
        if not isinstance(observations, ObservationIngressService):
            raise TypeError("invalid observation service")
        if not isinstance(understanding, UnderstandingService):
            raise TypeError("invalid understanding service")
        if not isinstance(recommendations, RecommendationService):
            raise TypeError("invalid recommendation service")
        self._settings = settings
        self._access = access
        self._source_status = GetSourceStatus(access)
        self._source_form = GetSourceForm(access)
        self._sources = ListSources(provider_ids, access)
        journal = _Journal()
        self._connect = ConnectSource(access, _Availability(), journal)
        self._disconnect = DisconnectSource(access, journal)
        self._recommendations = GetRecommendations(recommendations)
        self._record_observations = RecordObservations(observations)
        self._profile = ShowProfile(understanding)
        self._search = SearchContent(registry, access)
        self._details = GetContentDetails(registry, access)
        self._health = health

    async def source_status(self, provider_id: str, account_id: str | None) -> SourceStatusResult:
        return await self._source_status(
            GetSourceStatusQuery(provider_id=provider_id, account_id=account_id)
        )

    async def source_form(self, provider_id: str, method_id: str) -> ConnectionForm:
        return (
            await self._source_form(
                GetSourceFormQuery(provider_id=provider_id, method_id=method_id)
            )
        ).form

    async def list_sources(self, account_id: str | None, limit: int) -> SourcesResult:
        return await self._sources(ListSourcesQuery(account_id=account_id, limit=limit))

    async def connect_source(self, command: ConnectSourceCommand) -> ConnectSourceResult:
        return await self._connect(command)

    async def disconnect_source(self, command: DisconnectSourceCommand) -> AccessStatus:
        return await self._disconnect(command)

    async def get_recommendations(self, limit: int) -> RecommendationsResult:
        return await self._recommendations(GetRecommendationsQuery(limit=limit))

    async def record_observations(self, command: RecordObservationsCommand) -> RecordBatchResult:
        return await self._record_observations(command)

    async def show_profile(self, profile_id: str) -> ProfileResult:
        return await self._profile(ShowProfileQuery(profile_id=profile_id))

    async def search_content(self, provider_id: str, text: str, limit: int) -> SearchContentResult:
        return await self._search(
            SearchContentQuery(provider_id=ProviderId(value=provider_id), text=text, limit=limit)
        )

    async def get_content_details(self, reference: str) -> ContentDetailsResult:
        return await self._details(
            GetContentDetailsQuery(ref=ContentRef.model_validate_json(reference))
        )

    async def job_health(self) -> JobHealthResult:
        return JobHealthResult(health=self._health.health())

    async def config_diagnostics(self) -> DiagnosticResult:
        return DiagnosticResult(healthy=True, detail="configuration validated")

    async def model_diagnostics(self) -> DiagnosticResult:
        configured = bool(self._settings.model.model_name)
        return DiagnosticResult(
            healthy=configured,
            detail="model configured"
            if configured
            else "model provider is optional and unavailable",
        )

    async def start(self) -> StartResult:
        return StartResult(started=True)

    @staticmethod
    def _unavailable() -> ApplicationError:
        return ApplicationError(ApplicationErrorCode.UNAVAILABLE, "capability is not configured")

    async def refresh_recommendations(
        self, command: RefreshRecommendationsCommand
    ) -> RefreshRecommendationsResult:
        del command
        raise self._unavailable()

    async def record_feedback(self, command: RecordFeedbackCommand) -> RecordFeedbackResult:
        del command
        raise self._unavailable()

    async def edit_profile(self, command: EditProfileCommand) -> EditProfileResult:
        del command
        raise self._unavailable()

    async def propose_action(self, command: ProposeContentActionCommand) -> PendingAction:
        del command
        raise self._unavailable()

    async def confirm_action(self, command: ConfirmContentActionCommand) -> ActionResult:
        del command
        raise self._unavailable()

    async def assistant_turn(self, request: object, device_id: str) -> AssistantOutput:
        del request, device_id
        raise self._unavailable()

    async def conversation(self, conversation_id: str, device_id: str) -> Conversation:
        del conversation_id, device_id
        raise self._unavailable()

    async def conversation_messages(
        self, conversation_id: str, device_id: str, limit: int
    ) -> tuple[ConversationMessage, ...]:
        del conversation_id, device_id, limit
        raise self._unavailable()
