"""Concrete narrow host facade over target application workflows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.application.content_actions import (
    ConfirmProfileRevision,
    ConfirmProfileRevisionCommand,
    PendingAction,
    PendingActionResult,
    ProposeProfileRevision,
    ProposeProfileRevisionCommand,
    RejectPendingAction,
    RejectPendingActionCommand,
)
from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.application.plugin_access import (
    PluginAssistedAccess,
    SubmitAccessMaterialCommand,
)
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
from openbiliclaw.hosts.api.dependencies import AssistantTurnInput, DiagnosticResult, StartResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openbiliclaw.access.forms import ConnectionForm
    from openbiliclaw.access.models import AccessHandle, AccessStatus
    from openbiliclaw.access.service import AccessService
    from openbiliclaw.application.content_actions import (
        ConfirmContentActionCommand,
        ProposeContentActionCommand,
    )
    from openbiliclaw.application.edit_profile import (
        EditProfile,
        EditProfileCommand,
        EditProfileResult,
    )
    from openbiliclaw.application.external_evidence import (
        ExternalEvidenceIngestion,
        ExternalEvidenceResult,
    )
    from openbiliclaw.application.pending_actions import SqlitePendingActionRepository
    from openbiliclaw.application.record_feedback import (
        RecordFeedback,
        RecordFeedbackCommand,
        RecordFeedbackForShown,
        RecordFeedbackResult,
    )
    from openbiliclaw.application.refresh_recommendations import (
        RefreshRecommendations,
        RefreshRecommendationsCommand,
        RefreshRecommendationsResult,
    )
    from openbiliclaw.application.sources import IdempotencyJournal
    from openbiliclaw.assistant.models import (
        AssistantLifecycleEvent,
        AssistantOutput,
        Conversation,
        ConversationMessage,
    )
    from openbiliclaw.composition.assistant import AssistantController
    from openbiliclaw.content.integration.actions import ActionResult
    from openbiliclaw.content.integration.manifest import AccessRecipe
    from openbiliclaw.content.integration.registry import ContentProviderRegistry
    from openbiliclaw.core.config import AppSettings
    from openbiliclaw.core.health import HealthSnapshot
    from openbiliclaw.observations.service import ObservationIngressService, RecordBatchResult
    from openbiliclaw.recommendation.models import FeedbackKind
    from openbiliclaw.recommendation.service import RecommendationService
    from openbiliclaw.understanding.service import UnderstandingService


class _Availability:
    async def refresh(self, provider_id: str) -> None:
        del provider_id


class HealthSource(Protocol):
    def health(self) -> HealthSnapshot: ...


class _ContentVerifier:
    def __init__(self, registry: ContentProviderRegistry) -> None:
        self._registry = registry

    async def available(self, ref: ContentRef, handle: AccessHandle) -> bool:
        del handle
        try:
            self._registry.provider(ref.provider_id)
        except Exception:
            return False
        return True


class _ActionExecutor:
    def __init__(self, registry: ContentProviderRegistry) -> None:
        self._registry = registry

    async def execute(self, pending: PendingAction, handle: AccessHandle) -> ActionResult:
        from openbiliclaw.content.integration.actions import (
            ActionConfirmation,
            ActionRequest,
        )
        from openbiliclaw.content.integration.capabilities import ActionCapability

        if not isinstance(pending, PendingAction) or pending.ref is None:
            raise TypeError("invalid pending action")
        provider = self._registry.provider(pending.ref.provider_id)
        if not isinstance(provider, ActionCapability):
            raise CompositionFacade._unavailable()
        result: ActionResult = await provider.execute_action(
            ActionRequest(
                action_id=pending.action_id,
                ref=pending.ref,
                idempotency_key=pending.idempotency_key,
                confirmation=ActionConfirmation(
                    summary=pending.safe_preview, expires_at=pending.expires_at
                ),
            ),
            handle,
        )
        return result


class CompositionFacade:
    """Transport-complete facade; unavailable optional capabilities fail explicitly."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        access: AccessService,
        provider_ids: tuple[str, ...],
        registry: ContentProviderRegistry,
        observations: ObservationIngressService,
        understanding: UnderstandingService,
        recommendations: RecommendationService,
        health: HealthSource,
        idempotency: IdempotencyJournal,
        refresh: RefreshRecommendations | None = None,
        feedback: RecordFeedback | None = None,
        feedback_for_shown: RecordFeedbackForShown | None = None,
        external_evidence: ExternalEvidenceIngestion | None = None,
        profile_edit: EditProfile | None = None,
        pending_actions: SqlitePendingActionRepository | None = None,
        assistant: AssistantController | None = None,
    ) -> None:
        self._settings = settings
        self._access = access
        self._source_status = GetSourceStatus(access)
        self._source_form = GetSourceForm(access)
        self._sources = ListSources(provider_ids, access, recommendations)
        self._connect = ConnectSource(access, _Availability(), idempotency)
        self._disconnect = DisconnectSource(access, idempotency)
        self._plugin_access = PluginAssistedAccess(registry, access)
        self._recommendations = GetRecommendations(recommendations, clock=lambda: datetime.now(UTC))
        self._record_observations = RecordObservations(observations)
        self._profile = ShowProfile(understanding)
        self._search = SearchContent(registry, access)
        self._details = GetContentDetails(registry, access)
        self._registry = registry
        self._health = health
        self._refresh = refresh
        self._feedback = feedback
        self._feedback_for_shown = feedback_for_shown
        self._external_evidence = external_evidence
        self._profile_edit = profile_edit
        self._assistant = assistant
        self._propose = None
        self._confirm = None
        self._propose_profile = None
        self._confirm_profile = None
        self._reject_action = None
        if pending_actions is not None:
            from openbiliclaw.application.content_actions import (
                ConfirmContentAction,
                ProposeContentAction,
            )

            def clock() -> datetime:
                return datetime.now(UTC)

            self._propose = ProposeContentAction(pending_actions, clock)
            self._confirm = ConfirmContentAction(
                pending_actions,
                access,
                _ContentVerifier(registry),
                _ActionExecutor(registry),
                clock,
            )
            self._propose_profile = ProposeProfileRevision(pending_actions, understanding, clock)
            self._reject_action = RejectPendingAction(pending_actions, clock)
            if profile_edit is not None:
                self._confirm_profile = ConfirmProfileRevision(
                    pending_actions, profile_edit, understanding, clock
                )

    def set_assistant(self, assistant: AssistantController) -> None:
        """Complete the explicit circular tool/controller edge during composition."""
        if self._assistant is not None:
            raise RuntimeError("assistant is already configured")
        self._assistant = assistant

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

    def provider_capabilities(self, provider_id: str) -> tuple[str, ...]:
        manifest = self._registry.manifest(ProviderId(value=provider_id))
        return tuple(sorted(capability.value for capability in manifest.capabilities))

    async def connect_source(self, command: ConnectSourceCommand) -> ConnectSourceResult:
        return await self._connect(command)

    def access_recipe(self, provider_id: str) -> AccessRecipe:
        return self._plugin_access.recipe(provider_id)

    async def submit_access_material(self, command: SubmitAccessMaterialCommand) -> AccessStatus:
        return await self._plugin_access.submit(command)

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
        if self._refresh is None:
            raise self._unavailable()
        return await self._refresh(command)

    async def record_feedback(self, command: RecordFeedbackCommand) -> RecordFeedbackResult:
        if self._feedback is None:
            raise self._unavailable()
        return await self._feedback(command)

    async def record_feedback_for_shown(
        self, shown_id: str, kind: FeedbackKind, idempotency_key: str, exposed: bool = False
    ) -> RecordFeedbackResult:
        if self._feedback_for_shown is None:
            raise self._unavailable()
        return await self._feedback_for_shown(
            shown_id=shown_id, kind=kind, idempotency_key=idempotency_key, exposed=exposed
        )

    async def sync_source(self, provider_id: str) -> ExternalEvidenceResult:
        if self._external_evidence is None:
            raise self._unavailable()
        return await self._external_evidence.sync(provider_id)

    async def import_provider_evidence(
        self, provider_id: str, path: Path
    ) -> ExternalEvidenceResult:
        if self._external_evidence is None:
            raise self._unavailable()
        return await self._external_evidence.import_file(provider_id, path)

    async def edit_profile(self, command: EditProfileCommand) -> EditProfileResult:
        if self._profile_edit is None:
            raise self._unavailable()
        return await self._profile_edit(command)

    async def propose_action(self, command: ProposeContentActionCommand) -> PendingAction:
        if self._propose is None:
            raise self._unavailable()
        return await self._propose(command)

    async def propose_profile_revision(
        self, command: ProposeProfileRevisionCommand
    ) -> PendingAction:
        if self._propose_profile is None:
            raise self._unavailable()
        return await self._propose_profile(command)

    async def confirm_action(self, command: ConfirmContentActionCommand) -> PendingActionResult:
        if self._confirm is None or self._confirm_profile is None:
            raise self._unavailable()
        pending = await self._confirm_profile.repository.get(command.pending_action_id)
        if pending is not None and pending.kind == "profile_revision":
            return await self._confirm_profile(
                ConfirmProfileRevisionCommand.model_validate(command.model_dump())
            )
        return await self._confirm(command)

    async def reject_action(self, command: RejectPendingActionCommand) -> PendingAction:
        if self._reject_action is None:
            raise self._unavailable()
        return await self._reject_action(command)

    async def assistant_turn(self, request: AssistantTurnInput, device_id: str) -> AssistantOutput:
        if self._assistant is None:
            raise self._unavailable()
        return await self._assistant.turn(request, device_id)

    async def assistant_turn_stream(
        self, request: AssistantTurnInput, device_id: str
    ) -> AsyncIterator[AssistantLifecycleEvent]:
        if self._assistant is None:
            raise self._unavailable()
        async for event in self._assistant.stream_turn(request, device_id):
            yield event

    async def conversation(self, conversation_id: str, device_id: str) -> Conversation:
        if self._assistant is None:
            raise self._unavailable()
        return await self._assistant.conversation(conversation_id, device_id)

    async def conversation_messages(
        self, conversation_id: str, device_id: str, limit: int | None
    ) -> tuple[ConversationMessage, ...]:
        if self._assistant is None:
            raise self._unavailable()
        return await self._assistant.messages(conversation_id, device_id, limit)
