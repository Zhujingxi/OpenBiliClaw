from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.access.forms import ConnectionForm
from openbiliclaw.access.models import (
    AccessRequest,
    AccessStatus,
    AccessStatusKind,
    AnonymousAccessHandle,
    Permission,
)
from openbiliclaw.application.content_actions import (
    ConfirmContentAction,
    ConfirmContentActionCommand,
    InMemoryPendingActionRepository,
    PendingAction,
    ProposeContentAction,
    ProposeContentActionCommand,
)
from openbiliclaw.application.context import ApplicationContext
from openbiliclaw.application.edit_profile import EditProfile, EditProfileCommand
from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode
from openbiliclaw.application.record_feedback import RecordFeedback, RecordFeedbackCommand
from openbiliclaw.application.record_observation import (
    RecordObservations,
    RecordObservationsCommand,
)
from openbiliclaw.application.refresh_recommendations import (
    RefreshRecommendations,
    RefreshRecommendationsCommand,
)
from openbiliclaw.application.sources import (
    ConnectSource,
    ConnectSourceCommand,
    DisconnectSource,
    DisconnectSourceCommand,
)
from openbiliclaw.content.integration.actions import ActionResult
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.core.health import HealthSnapshot, HealthStatus
from openbiliclaw.core.jobs import JobDecision
from openbiliclaw.observations.models import RecommendationLikedObservation
from openbiliclaw.recommendation.models import (
    ExplorationAttribution,
    FeedbackKind,
    FeedbackRecord,
)
from openbiliclaw.understanding.overrides import OverrideOperation
from openbiliclaw.understanding.profile import CanonicalProfile

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.observations.models import Observation
    from openbiliclaw.observations.service import RecordBatchResult

NOW = datetime(2030, 1, 1, tzinfo=UTC)
REF = ContentRef(
    provider_id=ProviderId(value="demo"),
    content_kind=ContentKind(value="video"),
    provider_content_id="1",
    canonical_url="https://demo.example/1",
)


class AccessFake:
    def __init__(self) -> None:
        self.connects = 0
        self.handle: AnonymousAccessHandle | None = AnonymousAccessHandle(
            provider_id="demo",
            account_id=None,
            permissions=frozenset({Permission.READ_PUBLIC}),
        )

    async def connect(
        self,
        request: AccessRequest,
        *,
        allowed_method_ids: frozenset[str],
        submission: Mapping[str, str] | None,
    ) -> AccessStatus:
        self.connects += 1
        return AccessStatus(
            provider_id=request.provider_id,
            account_id=request.account_id,
            state=AccessStatusKind.CONNECTED,
            method_id="builtin.anonymous",
        )

    async def disconnect(self, provider_id: str, account_id: str | None) -> AccessStatus:
        return AccessStatus(
            provider_id=provider_id, account_id=account_id, state=AccessStatusKind.DISCONNECTED
        )

    async def status(self, provider_id: str, account_id: str | None) -> AccessStatus:
        return AccessStatus(
            provider_id=provider_id, account_id=account_id, state=AccessStatusKind.CONNECTED
        )

    def connected_handle(
        self, provider_id: str, account_id: str | None
    ) -> AnonymousAccessHandle | None:
        return self.handle


class JournalFake:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def put(self, key: str, value: str) -> None:
        self.values.setdefault(key, value)


class AvailabilityFake:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def refresh(self, provider_id: str) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("offline")


class UnitOfWorkFake:
    def __init__(self) -> None:
        self.feedback: list[tuple[FeedbackRecord, Observation]] = []
        self.edits: list[Observation] = []
        self.cancel = False

    async def record_feedback(
        self, feedback: FeedbackRecord, observation: Observation, content_ref: ContentRef
    ) -> bool:
        assert content_ref == REF
        if self.cancel:
            raise asyncio.CancelledError
        if any(item.feedback_id == feedback.feedback_id for item, _ in self.feedback):
            return False
        self.feedback.append((feedback, observation))
        return True

    async def edit_profile(
        self,
        profile_id: str,
        *,
        claim_id: str,
        operation: OverrideOperation,
        value: str | None,
        observation: Observation,
    ) -> CanonicalProfile:
        if not any(item.idempotency_key == observation.idempotency_key for item in self.edits):
            self.edits.append(observation)
        return CanonicalProfile.empty(profile_id, NOW)


async def test_connect_source_restores_missing_live_connection_on_cached_result() -> None:
    journal = JournalFake()
    command = ConnectSourceCommand(
        idempotency_key="connect:demo:restart",
        request=AccessRequest(
            provider_id="demo",
            permissions=frozenset({Permission.READ_PUBLIC}),
            supported_method_ids=("builtin.anonymous",),
        ),
        allowed_method_ids=frozenset({"builtin.anonymous"}),
    )
    first_access = AccessFake()
    await ConnectSource(first_access, AvailabilityFake(), journal)(command)

    restarted_access = AccessFake()
    restarted_access.handle = None
    result = await ConnectSource(restarted_access, AvailabilityFake(), journal)(command)

    assert result.status.state is AccessStatusKind.CONNECTED
    assert restarted_access.connects == 1


async def test_connect_source_is_idempotent_and_reports_recoverable_refresh_failure() -> None:
    access, journal = AccessFake(), JournalFake()
    workflow = ConnectSource(access, AvailabilityFake(fail=True), journal)
    command = ConnectSourceCommand(
        idempotency_key="connect:demo:1",
        request=AccessRequest(
            provider_id="demo",
            permissions=frozenset({Permission.READ_PUBLIC}),
            supported_method_ids=("builtin.anonymous",),
        ),
        allowed_method_ids=frozenset({"builtin.anonymous"}),
    )
    first = await workflow(command)
    second = await ConnectSource(access, AvailabilityFake(), journal)(command)
    assert first == second
    assert first.recoverable and first.status.state is AccessStatusKind.CONNECTED
    assert access.connects == 1


@pytest.mark.parametrize(
    "kind",
    [
        FeedbackKind.OPENED,
        FeedbackKind.LIKED,
        FeedbackKind.DISLIKED,
        FeedbackKind.SAVED,
        FeedbackKind.DISMISSED,
    ],
)
async def test_record_feedback_builds_each_typed_observation(kind: FeedbackKind) -> None:
    uow = UnitOfWorkFake()
    result = await RecordFeedback(uow, clock=lambda: NOW)(
        RecordFeedbackCommand(
            idempotency_key=f"feedback:{kind.value}:001",
            shown_id="shown_" + "1" * 32,
            content_ref=REF,
            kind=kind,
            account_id="acct",
            reason="not for me"
            if kind in {FeedbackKind.DISLIKED, FeedbackKind.DISMISSED}
            else None,
            dwell_ms=42 if kind is FeedbackKind.OPENED else None,
        )
    )
    assert result.inserted
    assert uow.feedback[0][1].event_type == f"recommendation_{kind.value}"


async def test_feedback_observation_carries_server_resolved_exploration_provenance() -> None:
    attribution = ExplorationAttribution(hypothesis_id="hyp_" + "a" * 32, arm="source-novel")
    targets = AsyncMock()
    targets.exploration_for_shown.return_value = attribution
    uow = UnitOfWorkFake()

    await RecordFeedback(uow, clock=lambda: NOW, targets=targets)(
        RecordFeedbackCommand(
            idempotency_key="feedback:explore:liked",
            shown_id="shown_" + "1" * 32,
            content_ref=REF,
            kind=FeedbackKind.LIKED,
            exposed=True,
        )
    )

    observation = uow.feedback[0][1]
    assert isinstance(observation, RecommendationLikedObservation)
    assert observation.payload.exploration_arm == attribution.arm
    assert observation.payload.exploration_hypothesis_id == attribution.hypothesis_id
    assert observation.payload.exposed is True


async def test_record_feedback_credits_reward_only_after_new_insert() -> None:
    uow = UnitOfWorkFake()
    reward = AsyncMock()
    workflow = RecordFeedback(uow, clock=lambda: NOW, reward_sink=reward)
    command = RecordFeedbackCommand(
        idempotency_key="feedback:reward:001",
        shown_id="shown_" + "1" * 32,
        content_ref=REF,
        kind=FeedbackKind.LIKED,
    )

    first = await workflow(command)
    second = await workflow(command)

    assert first.inserted and not second.inserted
    reward.assert_awaited_once_with(*uow.feedback[0])


async def test_reward_sink_failure_never_fails_a_committed_feedback() -> None:
    """Learning-plane errors (e.g. killed hypothesis) stay out of the feedback path."""

    uow = UnitOfWorkFake()
    reward = AsyncMock(side_effect=ValueError("hypothesis is killed"))
    workflow = RecordFeedback(uow, clock=lambda: NOW, reward_sink=reward)

    result = await workflow(
        RecordFeedbackCommand(
            idempotency_key="feedback:reward:sink-failure",
            shown_id="shown_" + "1" * 32,
            content_ref=REF,
            kind=FeedbackKind.LIKED,
        )
    )

    assert result.inserted
    reward.assert_awaited_once()


async def test_record_feedback_builds_typed_observation_in_one_uow_and_propagates_cancel() -> None:
    uow = UnitOfWorkFake()
    workflow = RecordFeedback(uow, clock=lambda: NOW)
    command = RecordFeedbackCommand(
        idempotency_key="feedback:shown:liked",
        shown_id="shown_" + "1" * 32,
        content_ref=REF,
        kind=FeedbackKind.LIKED,
        account_id="acct",
    )
    first = await workflow(command)
    second = await workflow(command)
    assert first.inserted and not second.inserted
    feedback, observation = uow.feedback[0]
    assert feedback.kind is FeedbackKind.LIKED
    assert observation.event_type == "recommendation_liked"
    assert observation.idempotency_key == command.idempotency_key
    uow.cancel = True
    with pytest.raises(asyncio.CancelledError):
        await workflow(command.model_copy(update={"idempotency_key": "feedback:other"}))


async def test_edit_profile_delegates_atomic_override_and_observation() -> None:
    uow = UnitOfWorkFake()
    command = EditProfileCommand(
        idempotency_key="profile:edit:0001",
        profile_id="default",
        account_id="acct",
        claim_id="claim_" + "a" * 32,
        operation=OverrideOperation.SET,
        value="science",
    )
    result = await EditProfile(uow, clock=lambda: NOW)(command)
    await EditProfile(uow, clock=lambda: NOW)(command)
    assert result.profile.profile_id == "default"
    assert uow.edits[0].event_type == "deterministic_profile_edit"
    assert len(uow.edits) == 1


class SupervisorFake:
    def __init__(self) -> None:
        self.calls = 0
        self.maximum_items: int | None = None

    def trigger(self, job_id: str, *, maximum_items: int) -> JobDecision:
        self.calls += 1
        self.maximum_items = maximum_items
        return JobDecision.RUN

    def health(self) -> HealthSnapshot:
        return HealthSnapshot(component_id="runtime", status=HealthStatus.HEALTHY, checked_at=NOW)


async def test_refresh_requests_bounded_supervised_work_without_creating_tasks() -> None:
    supervisor = SupervisorFake()
    result = await RefreshRecommendations(supervisor)(
        RefreshRecommendationsCommand(idempotency_key="refresh:00000001", maximum_items=20)
    )
    assert result.decision is JobDecision.RUN
    assert supervisor.calls == 1
    assert supervisor.maximum_items == 20


class ContentStateFake:
    def __init__(self) -> None:
        self.is_available = True

    async def available(self, ref: ContentRef, handle: object) -> bool:
        return self.is_available


class ActionProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, pending: PendingAction, handle: object) -> ActionResult:
        self.calls += 1
        return ActionResult(
            action_id=pending.action_id,
            ref=pending.ref,
            idempotency_key=pending.idempotency_key,
            completed_at=NOW,
        )


async def test_pending_action_requires_scope_confirmation_and_revalidates_access() -> None:
    repo = InMemoryPendingActionRepository()
    access = AccessFake()
    provider = ActionProvider()
    content = ContentStateFake()
    propose = ProposeContentAction(repo, clock=lambda: NOW)
    pending = await propose(
        ProposeContentActionCommand(
            idempotency_key="action:save:0001",
            action_id="save",
            ref=REF,
            user_id="user-1",
            account_id=None,
            safe_preview="Save this video?",
            expires_in_seconds=60,
        )
    )
    confirm = ConfirmContentAction(repo, access, content, provider, clock=lambda: NOW)
    with pytest.raises(ApplicationError) as wrong_user:
        await confirm(
            ConfirmContentActionCommand(pending_action_id=pending.pending_action_id, user_id="x")
        )
    assert wrong_user.value.code is ApplicationErrorCode.FORBIDDEN
    result = await confirm(
        ConfirmContentActionCommand(pending_action_id=pending.pending_action_id, user_id="user-1")
    )
    again = await confirm(
        ConfirmContentActionCommand(pending_action_id=pending.pending_action_id, user_id="user-1")
    )
    assert result == again
    assert provider.calls == 1

    missing = ConfirmContentAction(repo, access, content, provider, clock=lambda: NOW)
    with pytest.raises(ApplicationError) as not_found:
        await missing(
            ConfirmContentActionCommand(pending_action_id="pending_" + "f" * 32, user_id="user-1")
        )
    assert not_found.value.code is ApplicationErrorCode.NOT_FOUND

    expired = await propose(
        ProposeContentActionCommand(
            idempotency_key="action:save:0002",
            action_id="save",
            ref=REF,
            user_id="user-1",
            account_id=None,
            safe_preview="Save another?",
            expires_in_seconds=1,
        )
    )
    late = ConfirmContentAction(
        repo, access, content, provider, clock=lambda: NOW + timedelta(seconds=2)
    )
    with pytest.raises(ApplicationError) as error:
        await late(
            ConfirmContentActionCommand(
                pending_action_id=expired.pending_action_id, user_id="user-1"
            )
        )
    assert error.value.code is ApplicationErrorCode.EXPIRED

    access.handle = None
    inaccessible = await propose(
        ProposeContentActionCommand(
            idempotency_key="action:save:0003",
            action_id="save",
            ref=REF,
            user_id="user-1",
            account_id=None,
            safe_preview="Save inaccessible?",
            expires_in_seconds=60,
        )
    )
    with pytest.raises(ApplicationError) as unauthorized:
        await confirm(
            ConfirmContentActionCommand(
                pending_action_id=inaccessible.pending_action_id, user_id="user-1"
            )
        )
    assert unauthorized.value.code is ApplicationErrorCode.UNAUTHORIZED

    access.handle = AnonymousAccessHandle(
        provider_id="demo", account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )
    content.is_available = False
    unavailable = await propose(
        ProposeContentActionCommand(
            idempotency_key="action:save:0004",
            action_id="save",
            ref=REF,
            user_id="user-1",
            account_id=None,
            safe_preview="Save gone content?",
            expires_in_seconds=60,
        )
    )
    with pytest.raises(ApplicationError) as conflict:
        await confirm(
            ConfirmContentActionCommand(
                pending_action_id=unavailable.pending_action_id, user_id="user-1"
            )
        )
    assert conflict.value.code is ApplicationErrorCode.CONFLICT


class IngressFake:
    def __init__(self) -> None:
        self.calls = 0

    async def record_batch(
        self, observations: tuple[Observation, ...], *, allowed_event_types: frozenset[str]
    ) -> RecordBatchResult:
        from openbiliclaw.observations.service import (
            RecordBatchResult,
            RecordItemResult,
            RecordStatus,
        )

        self.calls += 1
        return RecordBatchResult(
            (RecordItemResult(0, RecordStatus.INSERTED, observations[0].observation_id),)
        )


async def test_record_observation_and_disconnect_delegate_with_idempotency() -> None:
    uow = UnitOfWorkFake()
    feedback_workflow = RecordFeedback(uow, clock=lambda: NOW)
    await feedback_workflow(
        RecordFeedbackCommand(
            idempotency_key="feedback:observe:1",
            shown_id="shown_" + "1" * 32,
            content_ref=REF,
            kind=FeedbackKind.LIKED,
            account_id="acct",
        )
    )
    event = uow.feedback[0][1]
    ingress = IngressFake()
    result = await RecordObservations(ingress)(
        RecordObservationsCommand(
            idempotency_key="batch:observe:01",
            observations=(event,),
            allowed_event_types=frozenset({event.event_type}),
        )
    )
    assert result.items[0].status.value == "inserted"

    access, journal = AccessFake(), JournalFake()
    command = DisconnectSourceCommand(idempotency_key="disconnect:demo:1", provider_id="demo")
    workflow = DisconnectSource(access, journal)
    assert (await workflow(command)).state is AccessStatusKind.DISCONNECTED
    assert (await workflow(command)).state is AccessStatusKind.DISCONNECTED


def test_context_is_frozen_and_explicit() -> None:
    context = ApplicationContext(access=AccessFake(), workflows=JournalFake())
    with pytest.raises(AttributeError):
        context.access = AccessFake()  # type: ignore[misc]


def _connection_form(method_id: str = "builtin.manual") -> ConnectionForm:
    from openbiliclaw.access.forms import ConnectionForm, FieldKind, FormField
    from openbiliclaw.access.models import InteractionKind

    return ConnectionForm(
        provider_id="demo",
        method_id=method_id,
        interaction=InteractionKind.SECRET_FORM,
        fields=(FormField(field_id="token", label="Token", kind=FieldKind.TOKEN, secret=True),),
    )


async def test_get_source_form_selects_real_service_form_port() -> None:
    from openbiliclaw.application.reads import GetSourceForm, GetSourceFormQuery

    class Forms:
        def connection_forms(self, provider_id: str) -> tuple[ConnectionForm, ...]:
            assert provider_id == "demo"
            return (_connection_form(),)

    result = await GetSourceForm(Forms())(
        GetSourceFormQuery(provider_id="demo", method_id="builtin.manual")
    )
    assert result.form.method_id == "builtin.manual"


async def test_get_source_form_not_found_is_stable() -> None:
    from openbiliclaw.application.reads import GetSourceForm, GetSourceFormQuery

    class Forms:
        def connection_forms(self, provider_id: str) -> tuple[ConnectionForm, ...]:
            return ()

    with pytest.raises(ApplicationError) as error:
        await GetSourceForm(Forms())(
            GetSourceFormQuery(provider_id="demo", method_id="builtin.manual")
        )
    assert error.value.code is ApplicationErrorCode.NOT_FOUND
