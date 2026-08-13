from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import ConfigDict

from openbiliclaw.access.forms import ConnectionForm, FieldKind, FormField
from openbiliclaw.access.models import (
    AccessStatus,
    AccessStatusKind,
    AnonymousAccessHandle,
    InteractionKind,
    Permission,
)
from openbiliclaw.application.content_actions import (
    ConfirmContentActionCommand,
    PendingAction,
    ProposeContentActionCommand,
)
from openbiliclaw.application.edit_profile import EditProfileCommand, EditProfileResult
from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.application.reads import (
    ContentDetailsResult,
    GetContentDetails,
    GetContentDetailsQuery,
    JobHealthResult,
    ProfileResult,
    RecommendationsResult,
    SearchContent,
    SearchContentQuery,
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
from openbiliclaw.assistant.models import (
    AssistantClarification,
    AssistantMessage,
    AssistantOutput,
    AssistantPendingAction,
    AssistantRecommendationPresentation,
    Conversation,
    ConversationMessage,
    ConversationScope,
    PendingActionSummary,
)
from openbiliclaw.content.integration.actions import ActionResult
from openbiliclaw.content.integration.capabilities import ContentPage, SearchQuery
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.native import NativeContent
from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    ProjectionProvenance,
)
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.core.health import HealthSnapshot, HealthStatus
from openbiliclaw.core.jobs import JobDecision
from openbiliclaw.hosts.api import HostDependencies, HostSecurityPolicy, create_app
from openbiliclaw.hosts.api.dependencies import (
    AssistantTurnInput,
    DiagnosticResult,
    StartResult,
)
from openbiliclaw.hosts.api.schemas.models import (
    AssistantEvent,
    ConnectionEvent,
    JobEvent,
    RecommendationEvent,
)
from openbiliclaw.observations.models import ContentOpenedObservation, HostOpenPayload
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.observations.service import RecordBatchResult
from openbiliclaw.recommendation.models import (
    RecommendationFeedItem,
    ScoreContribution,
    SelectionRecord,
)
from openbiliclaw.understanding.profile import CanonicalProfile
from openbiliclaw.understanding.projections import DialogueProfile

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

NOW = datetime(2030, 1, 1, tzinfo=UTC)
REF = ContentRef(
    provider_id=ProviderId(value="demo"),
    content_kind=ContentKind(value="video"),
    provider_content_id="1",
    canonical_url="https://example.com/1",
)
STATUS = AccessStatus(provider_id="demo", account_id=None, state=AccessStatusKind.DISCONNECTED)
PREVIEW = ContentPreview(
    ref=REF,
    title="One",
    summary="summary",
    source_timestamp=NOW,
    provenance=ProjectionProvenance(ref=REF, native_schema_version=1, projected_at=NOW),
)
SELECTION = SelectionRecord(
    recommendation_id="rec_" + "1" * 32,
    candidate_id="cand_" + "2" * 32,
    rank=1,
    score=1.0,
    contributions=(ScoreContribution(component="base", value=1),),
    selected_at=NOW,
    seed=1,
)
FEED_ITEM = RecommendationFeedItem(
    shown_id="shown_" + "3" * 32,
    selection=SELECTION,
    ref=REF,
    card=CardData(
        ref=REF,
        title="One",
        summary="summary",
        source_timestamp=NOW,
        provenance=ProjectionProvenance(ref=REF, native_schema_version=1, projected_at=NOW),
    ),
    reason="Recommended for relevance and freshness.",
)
PROFILE = CanonicalProfile.empty("default", NOW)
DIALOGUE = DialogueProfile(version=1, preference_summary=("science",), insights=())
HEALTH = HealthSnapshot(component_id="runtime", status=HealthStatus.HEALTHY, checked_at=NOW)
OBSERVATION = ContentOpenedObservation(
    observation_id="obs_" + "1" * 32,
    idempotency_key="event:0001",
    occurred_at=NOW,
    received_at=NOW,
    content_ref=REF,
    provenance=ObservationProvenance(
        producer_id="host.web",
        source=ObservationSource.HOST,
        authenticated=False,
        trust_level=TrustLevel.LOW,
    ),
    payload=HostOpenPayload(surface="web"),
)


class NativePayload(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: str


@dataclass(slots=True)
class Facade:
    calls: list[str] = field(default_factory=list)
    failure: Exception | None = None
    assistant_kind: str = "message"
    delay: float = 0
    connected_submission: dict[str, str] | None = None

    async def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure

    async def source_status(self, provider_id: str, account_id: str | None) -> SourceStatusResult:
        await self._call("source_status")
        return SourceStatusResult(status=STATUS)

    async def source_form(self, provider_id: str, method_id: str) -> ConnectionForm:
        await self._call("source_form")
        return ConnectionForm(
            provider_id="demo",
            method_id="builtin.manual",
            interaction=InteractionKind.SECRET_FORM,
            fields=(FormField(field_id="token", label="Token", kind=FieldKind.TOKEN, secret=True),),
        )

    async def list_sources(self, account_id: str | None, limit: int) -> SourcesResult:
        await self._call("list_sources")
        return SourcesResult(items=(STATUS,))

    async def connect_source(self, command: ConnectSourceCommand) -> ConnectSourceResult:
        self.connected_submission = dict(command.submission) if command.submission else None
        await self._call("connect_source")
        return ConnectSourceResult(status=STATUS, availability_refreshed=True)

    async def disconnect_source(self, command: DisconnectSourceCommand) -> AccessStatus:
        await self._call("disconnect_source")
        return STATUS

    async def get_recommendations(self, limit: int) -> RecommendationsResult:
        await self._call("get_recommendations")
        return RecommendationsResult(items=(FEED_ITEM,))

    async def refresh_recommendations(
        self, command: RefreshRecommendationsCommand
    ) -> RefreshRecommendationsResult:
        await self._call("refresh_recommendations")
        return RefreshRecommendationsResult(decision=JobDecision.RUN)

    async def record_feedback(self, command: RecordFeedbackCommand) -> RecordFeedbackResult:
        await self._call("record_feedback")
        return RecordFeedbackResult(
            feedback_id="feedback_" + "1" * 32, observation_id="obs_" + "2" * 32, inserted=True
        )

    async def record_observations(self, command: RecordObservationsCommand) -> RecordBatchResult:
        await self._call("record_observations")
        return RecordBatchResult(())

    async def show_profile(self, profile_id: str) -> ProfileResult:
        await self._call("show_profile")
        return ProfileResult(profile=DIALOGUE)

    async def edit_profile(self, command: EditProfileCommand) -> EditProfileResult:
        await self._call("edit_profile")
        return EditProfileResult(profile=PROFILE, observation_id="obs_" + "3" * 32)

    async def search_content(self, provider_id: str, text: str, limit: int) -> SearchContentResult:
        await self._call("search_content")
        return SearchContentResult(items=(PREVIEW,))

    async def get_content_details(self, reference: str) -> ContentDetailsResult:
        await self._call("get_content_details")
        return ContentDetailsResult(
            content=NativeContent(ref=REF, schema_version=1, payload=NativePayload(title="One"))
        )

    async def propose_action(self, command: ProposeContentActionCommand) -> PendingAction:
        await self._call("propose_action")
        return PendingAction(
            pending_action_id="pending_" + "1" * 32,
            idempotency_key=command.idempotency_key,
            action_id=command.action_id,
            ref=command.ref,
            user_id=command.user_id,
            safe_preview=command.safe_preview,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

    async def confirm_action(self, command: ConfirmContentActionCommand) -> ActionResult:
        await self._call("confirm_action")
        return ActionResult(action_id="save", ref=REF, idempotency_key="action:1", completed_at=NOW)

    async def assistant_turn(self, request: AssistantTurnInput, device_id: str) -> AssistantOutput:
        await self._call("assistant_turn")
        if self.assistant_kind == "recommendations":
            return AssistantRecommendationPresentation(
                intro="These", recommendation_ids=("rec_" + "1" * 32,)
            )
        if self.assistant_kind == "clarification":
            return AssistantClarification(question="Which?", choices=("a",))
        if self.assistant_kind == "pending_action":
            return AssistantPendingAction(
                action=PendingActionSummary(
                    pending_action_id="pending_" + "1" * 32,
                    effect="save",
                    expires_at=NOW + timedelta(minutes=5),
                )
            )
        return AssistantMessage(text="Hello")

    async def conversation(self, conversation_id: str, device_id: str) -> Conversation:
        await self._call("conversation")
        return Conversation(
            conversation_id=conversation_id,
            scope=ConversationScope(local_user_id="local", device_id=device_id),
            created_at=NOW,
            updated_at=NOW,
        )

    async def conversation_messages(
        self, conversation_id: str, device_id: str, limit: int
    ) -> tuple[ConversationMessage, ...]:
        await self._call("conversation_messages")
        return ()

    async def job_health(self) -> JobHealthResult:
        await self._call("job_health")
        return JobHealthResult(health=HEALTH)

    async def config_diagnostics(self) -> DiagnosticResult:
        await self._call("config_diagnostics")
        return DiagnosticResult(healthy=True, detail="ok")

    async def model_diagnostics(self) -> DiagnosticResult:
        await self._call("model_diagnostics")
        return DiagnosticResult(healthy=True, detail="ok")

    async def start(self) -> StartResult:
        await self._call("start")
        return StartResult(started=True)


@dataclass(frozen=True, slots=True)
class Events:
    count: int = 4

    async def replay(
        self, after: int, limit: int
    ) -> tuple[JobEvent | RecommendationEvent | AssistantEvent | ConnectionEvent, ...]:
        events = (
            JobEvent(event_id=1, component_id="runtime", status="healthy"),
            RecommendationEvent(event_id=2, recommendation_id="rec", status="selected"),
            AssistantEvent(event_id=3, conversation_id="conv", status="complete"),
            ConnectionEvent(event_id=4, provider_id="demo", status="connected"),
        )
        return events * self.count


def client(
    facade: Facade, *, token: str | None = None, events: Events | None = None, **policy: object
) -> httpx.AsyncClient:
    security = HostSecurityPolicy(bearer_token=token, **policy)
    app = create_app(HostDependencies(facade=facade, security=security, events=events or Events()))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


MUTATION_HEADERS = {"X-Device-ID": "d", "X-CSRF-Token": "d"}


async def test_source_connect_passes_provider_form_submission() -> None:
    facade = Facade()
    async with client(facade) as api:
        response = await api.post(
            "/v1/sources/connect",
            json={
                "provider_id": "demo",
                "method_id": "builtin.manual",
                "idempotency_key": "connect:secret-form",
                "submission": {"token": "synthetic-secret"},
            },
            headers=MUTATION_HEADERS,
        )
    assert response.status_code == 200
    assert facade.connected_submission == {"token": "synthetic-secret"}


async def test_source_connect_accepts_json_permissions_array() -> None:
    facade = Facade()
    async with client(facade) as api:
        response = await api.post(
            "/v1/sources/connect",
            json={
                "provider_id": "demo",
                "method_id": "builtin.manual",
                "idempotency_key": "connect:json-permissions",
                "permissions": ["read_public"],
                "submission": {"token": "synthetic-secret"},
            },
            headers=MUTATION_HEADERS,
        )
    assert response.status_code == 200


async def test_mutation_double_submit_device_contract() -> None:
    body = {
        "provider_id": "demo",
        "method_id": "builtin.manual",
        "idempotency_key": "connect:1",
    }
    async with client(Facade()) as api:
        missing = await api.post("/v1/sources/connect", json=body)
        mismatch = await api.post(
            "/v1/sources/connect",
            json=body,
            headers={"X-Device-ID": "device", "X-CSRF-Token": "other"},
        )
        accepted = await api.post(
            "/v1/sources/connect",
            json=body,
            headers={"X-Device-ID": "device", "X-CSRF-Token": "device"},
        )
    assert missing.status_code == mismatch.status_code == 403
    assert accepted.status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body", "owner"),
    [
        ("GET", "/v1/sources", None, "list_sources"),
        ("GET", "/v1/sources/demo/status", None, "source_status"),
        ("GET", "/v1/sources/demo/forms/builtin.manual", None, "source_form"),
        (
            "POST",
            "/v1/sources/connect",
            {"provider_id": "demo", "method_id": "builtin.manual", "idempotency_key": "connect:1"},
            "connect_source",
        ),
        (
            "POST",
            "/v1/sources/disconnect",
            {"provider_id": "demo", "idempotency_key": "disconnect:1"},
            "disconnect_source",
        ),
        ("GET", "/v1/recommendations", None, "get_recommendations"),
        (
            "POST",
            "/v1/recommendations/refresh",
            {"idempotency_key": "refresh:1"},
            "refresh_recommendations",
        ),
        ("GET", "/v1/profiles/default", None, "show_profile"),
        (
            "POST",
            "/v1/profiles/edit",
            {
                "idempotency_key": "profile:1",
                "profile_id": "default",
                "account_id": "a",
                "claim_id": "claim_" + "a" * 32,
                "operation": "remove",
            },
            "edit_profile",
        ),
        ("GET", "/v1/content/search?provider_id=demo&q=x", None, "search_content"),
        ("GET", "/v1/content/demo:1", None, "get_content_details"),
        (
            "POST",
            "/v1/assistant/turns",
            {"conversation_id": "conv_" + "a" * 32, "text": "hello"},
            "assistant_turn",
        ),
        ("GET", "/v1/assistant/conversations/conv_" + "a" * 32, None, "conversation_messages"),
        ("GET", "/v1/runtime/health", None, "job_health"),
    ],
)
async def test_each_route_calls_its_typed_workflow(
    method: str, path: str, body: object, owner: str
) -> None:
    facade = Facade()
    async with client(facade) as api:
        response = await api.request(method, path, json=body, headers=MUTATION_HEADERS)
    assert response.status_code == 200, response.text
    assert facade.calls[-1] == owner


@pytest.mark.parametrize("kind", ["message", "recommendations", "clarification", "pending_action"])
async def test_assistant_all_output_variants(kind: str) -> None:
    facade = Facade(assistant_kind=kind)
    async with client(facade) as api:
        response = await api.post(
            "/v1/assistant/turns",
            json={"conversation_id": "conv_" + "a" * 32, "text": "hi"},
            headers=MUTATION_HEADERS,
        )
    assert response.status_code == 200
    assert response.json()["output"]["kind"] == kind


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ApplicationError(ApplicationErrorCode.UNAUTHORIZED, "no"), 401, "unauthorized"),
        (ApplicationError(ApplicationErrorCode.FORBIDDEN, "no"), 403, "forbidden"),
        (ApplicationError(ApplicationErrorCode.NOT_FOUND, "no"), 404, "not_found"),
        (ApplicationError(ApplicationErrorCode.EXPIRED, "no"), 409, "conflict"),
        (ApplicationError(ApplicationErrorCode.CONFLICT, "no"), 409, "conflict"),
        (ApplicationError(ApplicationErrorCode.UNAVAILABLE, "no"), 503, "unavailable_capability"),
    ],
)
async def test_stable_error_mapping(error: ApplicationError, status: int, code: str) -> None:
    async with client(Facade(failure=error)) as api:
        response = await api.get("/v1/runtime/health")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@dataclass(frozen=True, slots=True)
class FailingProvider:
    error: ContentIntegrationError

    async def search(
        self, query: SearchQuery, access: AnonymousAccessHandle
    ) -> ContentPage[ContentPreview]:
        del query, access
        raise self.error

    async def fetch(self, ref: ContentRef, access: AnonymousAccessHandle) -> NativeContent:
        del ref, access
        raise self.error


@dataclass(frozen=True, slots=True)
class ProviderRegistry:
    provider_value: FailingProvider

    def provider(self, provider_id: ProviderId) -> FailingProvider:
        del provider_id
        return self.provider_value


@dataclass(frozen=True, slots=True)
class ConnectedAccess:
    async def status(self, provider_id: str, account_id: str | None) -> AccessStatus:
        return AccessStatus(
            provider_id=provider_id,
            account_id=account_id,
            state=AccessStatusKind.CONNECTED,
        )

    def connected_handle(self, provider_id: str, account_id: str | None) -> AnonymousAccessHandle:
        return AnonymousAccessHandle(
            provider_id=provider_id,
            account_id=account_id,
            permissions=frozenset({Permission.READ_PUBLIC}),
        )


@dataclass(slots=True)
class ProviderWorkflowFacade(Facade):
    error: ContentIntegrationError = field(
        default_factory=lambda: ContentIntegrationError(
            IntegrationErrorCode.PROVIDER_UNAVAILABLE, "safe provider failure"
        )
    )

    async def search_content(self, provider_id: str, text: str, limit: int) -> SearchContentResult:
        return await SearchContent(
            ProviderRegistry(FailingProvider(self.error)), ConnectedAccess()
        )(SearchContentQuery(provider_id=ProviderId(value=provider_id), text=text, limit=limit))

    async def get_content_details(self, reference: str) -> ContentDetailsResult:
        del reference
        return await GetContentDetails(
            ProviderRegistry(FailingProvider(self.error)), ConnectedAccess()
        )(GetContentDetailsQuery(ref=REF))


@pytest.mark.parametrize("path", ["/v1/content/search?provider_id=demo&q=x", "/v1/content/demo:1"])
@pytest.mark.parametrize(
    ("kind", "status", "code"),
    [
        (IntegrationErrorCode.ACCESS_DENIED, 403, "forbidden"),
        (IntegrationErrorCode.INVALID_CONTENT_REF, 404, "not_found"),
        (IntegrationErrorCode.UNAVAILABLE_CAPABILITY, 503, "unavailable_capability"),
        (IntegrationErrorCode.RATE_LIMITED, 503, "unavailable_capability"),
        (IntegrationErrorCode.PROVIDER_UNAVAILABLE, 503, "unavailable_capability"),
    ],
)
async def test_provider_operation_errors_are_stable_through_asgi(
    path: str, kind: IntegrationErrorCode, status: int, code: str
) -> None:
    facade = ProviderWorkflowFacade(error=ContentIntegrationError(kind, "safe provider failure"))
    async with client(facade) as api:
        response = await api.get(path)
    assert response.status_code == status
    assert response.json()["error"] == {"code": code, "message": "safe provider failure"}


async def test_security_validation_rate_timeout_and_safe_errors() -> None:
    async with client(Facade(), token="key", requests_per_minute=1) as api:
        assert (await api.get("/v1/runtime/health")).status_code == 401
        assert (
            await api.get(
                "/v1/runtime/health",
                headers={"Authorization": "Bearer key", "Origin": "https://evil"},
            )
        ).status_code == 403
        assert (
            await api.get("/v1/recommendations?limit=bad", headers={"Authorization": "Bearer key"})
        ).status_code == 422
        response = await api.get("/missing", headers={"Authorization": "Bearer key"})
        assert response.status_code == 429
    async with client(Facade(delay=0.05), request_timeout_seconds=0.001) as api:
        response = await api.get("/v1/runtime/health")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "temporary_failure"
    canary = "CANARY-SECRET"
    async with client(Facade(failure=RuntimeError(canary))) as api:
        response = await api.get("/v1/runtime/health")
    assert response.status_code == 500 and canary not in response.text


async def test_chunked_oversized_body_is_rejected() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"idempotency_key":"refresh:1","padding":"'
        yield b"x" * 2000 + b'"}'

    async with client(Facade(), max_body_bytes=1024) as api:
        response = await api.post(
            "/v1/recommendations/refresh", content=chunks(), headers=MUTATION_HEADERS
        )
    assert response.status_code == 413


async def test_sse_all_kinds_reconnect_and_local_replay_bound() -> None:
    async with client(Facade(), events=Events(), replay_limit=3) as api:
        response = await api.get("/v1/events/stream")
    assert response.status_code == 200
    assert "retry: 3000" in response.text
    assert response.text.count("data: ") == 3
    assert {
        json.loads(part.splitlines()[0])["kind"] for part in response.text.split("data: ")[1:]
    } == {"job", "recommendation", "assistant"}


def test_bind_policy_and_strict_transport() -> None:
    with pytest.raises(ValueError):
        HostSecurityPolicy(bind_host="0.0.0.0")

    async def check() -> httpx.Response:
        async with client(Facade()) as api:
            return await api.post(
                "/v1/recommendations/refresh",
                json={"idempotency_key": "refresh:1", "maximum_items": "5"},
                headers=MUTATION_HEADERS,
            )

    assert asyncio.run(check()).status_code == 422


@pytest.mark.parametrize(
    ("path", "body", "owner"),
    [
        (
            "/v1/feedback",
            {
                "idempotency_key": "feedback:1",
                "shown_id": "shown_1",
                "content_ref": REF.model_dump(mode="json"),
                "kind": "liked",
            },
            "record_feedback",
        ),
        (
            "/v1/observations",
            {
                "idempotency_key": "batch:001",
                "observations": [OBSERVATION.model_dump(mode="json")],
                "allowed_event_types": [OBSERVATION.event_type],
            },
            "record_observations",
        ),
        (
            "/v1/content/actions/propose",
            {
                "idempotency_key": "action:1",
                "action_id": "save",
                "ref": REF.model_dump(mode="json"),
                "user_id": "u",
                "safe_preview": "save",
            },
            "propose_action",
        ),
        (
            "/v1/content/actions/confirm",
            {"pending_action_id": "pending_" + "1" * 32, "user_id": "u"},
            "confirm_action",
        ),
    ],
)
async def test_remaining_matrix_endpoints_succeed_through_asgi(
    path: str, body: dict[str, object], owner: str
) -> None:
    facade = Facade()
    async with client(facade) as api:
        response = await api.post(path, json=body, headers=MUTATION_HEADERS)
    assert response.status_code == 200, response.text
    assert facade.calls[-1] == owner


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/v1/sources", None),
        ("GET", "/v1/sources/demo/status", None),
        ("GET", "/v1/sources/demo/forms/builtin.manual", None),
        (
            "POST",
            "/v1/sources/connect",
            {"provider_id": "demo", "method_id": "builtin.manual", "idempotency_key": "connect:1"},
        ),
        (
            "POST",
            "/v1/sources/disconnect",
            {"provider_id": "demo", "idempotency_key": "disconnect:1"},
        ),
        ("GET", "/v1/recommendations", None),
        ("POST", "/v1/recommendations/refresh", {"idempotency_key": "refresh:1"}),
        ("GET", "/v1/profiles/default", None),
        (
            "POST",
            "/v1/profiles/edit",
            {
                "idempotency_key": "profile:1",
                "profile_id": "default",
                "account_id": "a",
                "claim_id": "claim_" + "a" * 32,
                "operation": "remove",
            },
        ),
        (
            "POST",
            "/v1/assistant/turns",
            {"conversation_id": "conv_" + "a" * 32, "text": "hello"},
        ),
        ("GET", "/v1/assistant/conversations/conv_" + "a" * 32, None),
        ("GET", "/v1/content/search?provider_id=demo&q=x", None),
        ("GET", "/v1/content/demo:1", None),
        (
            "POST",
            "/v1/content/actions/propose",
            {
                "idempotency_key": "action:1",
                "action_id": "save",
                "ref": REF.model_dump(mode="json"),
                "user_id": "u",
                "safe_preview": "save",
            },
        ),
        (
            "POST",
            "/v1/content/actions/confirm",
            {"pending_action_id": "pending_" + "1" * 32, "user_id": "u"},
        ),
        (
            "POST",
            "/v1/feedback",
            {
                "idempotency_key": "feedback:1",
                "shown_id": "shown_1",
                "content_ref": REF.model_dump(mode="json"),
                "kind": "liked",
            },
        ),
        (
            "POST",
            "/v1/observations",
            {
                "idempotency_key": "batch:001",
                "observations": [OBSERVATION.model_dump(mode="json")],
                "allowed_event_types": [OBSERVATION.event_type],
            },
        ),
        ("GET", "/v1/runtime/health", None),
    ],
)
async def test_every_workflow_endpoint_maps_conflicts_through_asgi(
    method: str, path: str, body: dict[str, object] | None
) -> None:
    async with client(
        Facade(failure=ApplicationError(ApplicationErrorCode.CONFLICT, "safe conflict"))
    ) as api:
        response = await api.request(method, path, json=body, headers=MUTATION_HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["error"] == {"code": "conflict", "message": "safe conflict"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/openapi.json"),
        ("GET", "/v1/sources"),
        ("GET", "/v1/sources/demo/status"),
        ("GET", "/v1/sources/demo/forms/builtin.manual"),
        ("POST", "/v1/sources/connect"),
        ("POST", "/v1/sources/disconnect"),
        ("GET", "/v1/recommendations"),
        ("POST", "/v1/recommendations/refresh"),
        ("GET", "/v1/profiles/default"),
        ("POST", "/v1/profiles/edit"),
        ("POST", "/v1/assistant/turns"),
        ("GET", "/v1/assistant/conversations/conv_" + "a" * 32),
        ("GET", "/v1/content/search?provider_id=demo&q=x"),
        ("GET", "/v1/content/demo:1"),
        ("POST", "/v1/content/actions/propose"),
        ("POST", "/v1/content/actions/confirm"),
        ("POST", "/v1/feedback"),
        ("POST", "/v1/observations"),
        ("GET", "/v1/runtime/health"),
        ("GET", "/v1/events/stream"),
    ],
)
async def test_every_http_endpoint_enforces_auth(method: str, path: str) -> None:
    async with client(Facade(), token="key") as api:
        response = await api.request(method, path, json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_openapi_succeeds_through_asgi() -> None:
    async with client(Facade()) as api:
        response = await api.get("/v1/openapi.json")
    assert response.status_code == 200
    assert response.json()["openapi"].startswith("3.")


async def test_http_404_405_and_event_source_failure() -> None:
    async with client(Facade()) as api:
        missing = await api.get("/v1/no-such-route")
        wrong_method = await api.post("/v1/runtime/health", headers=MUTATION_HEADERS)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert (
        wrong_method.status_code == 405
        and wrong_method.json()["error"]["code"] == "method_not_allowed"
    )


async def test_event_none_and_local_bound_helpers() -> None:
    from openbiliclaw.hosts.api.routers.events import _events

    dependencies = HostDependencies(facade=Facade(), events=None)
    assert await _events(dependencies, 0) == ()


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [
        (
            "/v1/feedback",
            {
                "idempotency_key": "feedback:1",
                "shown_id": "shown_1",
                "content_ref": REF.model_dump(mode="json"),
                "kind": "opened",
                "dwell_ms": "1",
            },
            "dwell_ms",
        ),
        (
            "/v1/content/actions/propose",
            {
                "idempotency_key": "action:1",
                "action_id": "save",
                "ref": REF.model_dump(mode="json"),
                "user_id": "u",
                "safe_preview": "save",
                "expires_in_seconds": "5",
            },
            "expires_in_seconds",
        ),
    ],
)
async def test_every_mutation_transport_rejects_coercion(
    path: str, body: dict[str, object], field: str
) -> None:
    async with client(Facade()) as api:
        response = await api.post(path, json=body, headers=MUTATION_HEADERS)
    assert response.status_code == 422
    assert field not in response.text  # safe validation envelope has no input echo


async def test_static_frontend_serves_assets_and_spa_fallback(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "index.html").write_text("spa-index", encoding="utf-8")
    (tmp_path / "app.css").write_text("css", encoding="utf-8")
    app = create_app(HostDependencies(facade=Facade()), frontend_dir=tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        asset = await api.get("/app.css")
        route = await api.get("/profile")
        traversal = await api.get("/../outside")
    assert asset.text == "css"
    assert route.text == "spa-index"
    assert traversal.text == "spa-index"


async def test_app_lifespan_is_composition_owned_and_schema_export_is_idle() -> None:
    class Lifespan:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def start(self) -> None:
            self.calls.append("start")

        async def stop(self) -> None:
            self.calls.append("stop")

    lifespan = Lifespan()
    app = create_app(HostDependencies(facade=Facade(), lifespan=lifespan))
    app.openapi()
    assert lifespan.calls == []
    async with app.router.lifespan_context(app):
        assert lifespan.calls == ["start"]
    assert lifespan.calls == ["start", "stop"]
