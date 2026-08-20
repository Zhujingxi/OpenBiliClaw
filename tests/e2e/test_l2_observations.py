"""L2: real observations over content acquired from Bilibili."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.access.models import (
    AccessStatus,
    AccessStatusKind,
    Permission,
    VerificationResult,
    VerificationStrength,
)
from openbiliclaw.application.reads import SourceStatusResult
from openbiliclaw.application.record_feedback import RecordFeedbackCommand
from openbiliclaw.application.record_observation import RecordObservationsCommand
from openbiliclaw.application.sources import ConnectSourceResult
from openbiliclaw.composition.build import BuildOptions, build_application, validated_settings
from openbiliclaw.content.integration.capabilities import PageRequest
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.providers.bilibili.capabilities import BilibiliProvider
from openbiliclaw.observations.models import (
    ContentOpenedObservation,
    HistoryImportPayload,
    HostOpenPayload,
    ProviderHistoryImportObservation,
    RecommendationFeedbackPayload,
    RecommendationLikedObservation,
)
from openbiliclaw.observations.provenance import (
    ObservationProvenance,
    ObservationSource,
    TrustLevel,
)
from openbiliclaw.observations.service import RecordStatus
from openbiliclaw.recommendation.models import FeedbackKind

from .bilibili_chrome import BrowserCookies, connect_command, extract_bilibili_cookies
from .public_access import ensure_bilibili_public_access

if TYPE_CHECKING:
    from openbiliclaw.composition.application import Application
    from openbiliclaw.composition.facade import CompositionFacade

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l2, pytest.mark.asyncio]
_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _ROOT / "data-e2e"


async def _application() -> Application:
    settings = validated_settings(_DATA_DIR / "config.e2e.toml")
    application = build_application(settings, options=BuildOptions(data_dir=_DATA_DIR))
    await application.start()
    return application


def _observation_id() -> str:
    return f"obs_{uuid.uuid4().hex}"


def _host_opened(ref: ContentRef, key: str, now: datetime) -> ContentOpenedObservation:
    return ContentOpenedObservation(
        observation_id=_observation_id(),
        idempotency_key=key,
        occurred_at=now,
        received_at=now,
        content_ref=ref,
        provenance=ObservationProvenance(
            producer_id="host.e2e",
            source=ObservationSource.HOST,
            authenticated=False,
            trust_level=TrustLevel.LOW,
        ),
        payload=HostOpenPayload(surface="e2e"),
    )


def _liked(ref: ContentRef, key: str, now: datetime) -> RecommendationLikedObservation:
    return RecommendationLikedObservation(
        observation_id=_observation_id(),
        idempotency_key=key,
        occurred_at=now,
        received_at=now,
        content_ref=ref,
        provenance=ObservationProvenance(
            producer_id="application.e2e",
            source=ObservationSource.RECOMMENDATION,
            authenticated=False,
            trust_level=TrustLevel.LOW,
        ),
        payload=RecommendationFeedbackPayload(),
    )


async def test_liked_uses_neutral_recommendation_feedback_payload() -> None:
    liked = _liked(
        ContentRef(
            provider_id=ProviderId(value="bilibili"),
            content_kind=ContentKind(value="video"),
            provider_content_id="BV1contract",
            canonical_url="https://www.bilibili.com/video/BV1contract",
        ),
        "e2e:l2:liked:contract",
        datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert isinstance(liked.payload, RecommendationFeedbackPayload)
    assert liked.payload.exploration_arm is None
    assert liked.payload.exploration_hypothesis_id is None
    assert liked.payload.exposed is False


async def _real_public_ref(application: Application) -> ContentRef:
    facade = application.services.facade
    assert facade is not None
    await ensure_bilibili_public_access(cast("CompositionFacade", facade))
    results = await facade.search_content("bilibili", "Python", 1)
    assert results.items
    return results.items[0].ref


def _access_status(permissions: frozenset[Permission]) -> AccessStatus:
    return AccessStatus(
        provider_id="bilibili",
        account_id=None,
        state=AccessStatusKind.CONNECTED,
        method_id="builtin.manual",
        verification=VerificationResult(
            strength=VerificationStrength.LIVE,
            verified_at=datetime.now(UTC),
            granted_permissions=permissions,
        ),
    )


@pytest.mark.parametrize("already_connected", [False, True])
async def test_public_access_connects_only_when_disconnected(already_connected: bool) -> None:
    facade = AsyncMock()
    connected = _access_status(frozenset({Permission.READ_PUBLIC}))
    facade.source_status.return_value = SourceStatusResult(
        status=(
            connected
            if already_connected
            else AccessStatus(
                provider_id="bilibili", account_id=None, state=AccessStatusKind.DISCONNECTED
            )
        )
    )
    facade.connect_source.return_value = ConnectSourceResult(
        status=connected, availability_refreshed=True
    )

    await ensure_bilibili_public_access(cast("CompositionFacade", facade))

    if already_connected:
        facade.connect_source.assert_not_awaited()
    else:
        facade.connect_source.assert_awaited_once()
        command = facade.connect_source.await_args.args[0]
        assert command.request.permissions == frozenset({Permission.READ_PUBLIC})
        assert command.allowed_method_ids == frozenset({"builtin.anonymous"})


async def test_public_access_rejects_connected_handle_without_public_read() -> None:
    facade = AsyncMock()
    facade.source_status.return_value = SourceStatusResult(
        status=_access_status(frozenset({Permission.READ_PRIVATE}))
    )

    with pytest.raises(AssertionError, match="granting read_public; got state=connected"):
        await ensure_bilibili_public_access(cast("CompositionFacade", facade))

    facade.connect_source.assert_not_awaited()


async def test_content_landing_dedupe_feedback_durability_and_cursor_replay() -> None:
    run = uuid.uuid4().hex
    first = await _application()
    try:
        facade = first.services.facade
        assert facade is not None
        ref = await _real_public_ref(first)
        now = datetime.now(UTC)
        opened = _host_opened(ref, f"e2e:l2:opened:{run}", now)
        liked = _liked(ref, f"e2e:l2:liked:{run}", now)
        command = RecordObservationsCommand(
            idempotency_key=f"e2e:l2:batch:{run}",
            observations=(opened, liked),
            allowed_event_types=frozenset({"content_opened", "recommendation_liked"}),
        )
        inserted = await facade.record_observations(command)
        assert tuple(item.status for item in inserted.items) == (
            RecordStatus.INSERTED,
            RecordStatus.INSERTED,
        )
        duplicate = await facade.record_observations(command)
        assert tuple(item.status for item in duplicate.items) == (
            RecordStatus.DUPLICATE,
            RecordStatus.DUPLICATE,
        )

        delivered = await facade.get_recommendations(20)
        assert delivered.items
        feedback_item = delivered.items[0]
        feedback_key = f"e2e:l2:feedback:{run}"
        feedback = RecordFeedbackCommand(
            idempotency_key=feedback_key,
            shown_id=feedback_item.shown_id,
            content_ref=feedback_item.ref,
            kind=FeedbackKind.SAVED,
        )
        first_feedback = await facade.record_feedback(feedback)
        second_feedback = await facade.record_feedback(feedback)
        assert first_feedback.inserted
        assert not second_feedback.inserted

        assert first.resources is not None
        matching_refs = await first.resources.database.fetch_value(
            "SELECT COUNT(*) FROM content_references WHERE provider=? AND external_id=?",
            (ref.provider_id.value, ref.provider_content_id),
        )
        assert matching_refs == 1
    finally:
        await first.stop()

    restarted = await _application()
    try:
        observations = restarted.services.observations
        assert observations is not None
        page = await observations.query(after_cursor=None, limit=500)
        expected = (opened.observation_id, liked.observation_id, first_feedback.observation_id)
        seen = tuple(
            item.observation_id for item in page.items if item.observation_id in frozenset(expected)
        )
        assert seen == expected
        cursor_page = await observations.query(after_cursor=None, limit=1)
        assert cursor_page.next_cursor is not None
        remainder = await observations.query(after_cursor=cursor_page.next_cursor, limit=500)
        assert not any(
            item.observation_id == cursor_page.items[0].observation_id for item in remainder.items
        )
    finally:
        await restarted.stop()


async def test_authenticated_history_bootstrap_lands_provider_observations() -> None:
    cookies: BrowserCookies = extract_bilibili_cookies()
    application = await _application()
    try:
        facade = application.services.facade
        assert facade is not None
        await facade.connect_source(connect_command(cookies, f"e2e:l2:auth:{uuid.uuid4().hex}"))
        assert application.providers is not None
        provider = application.providers.registry.provider(ProviderId(value="bilibili"))
        assert isinstance(provider, BilibiliProvider)
        handle = cast("CompositionFacade", facade)._access.connected_handle(  # noqa: SLF001
            "bilibili", None
        )
        assert handle is not None
        history = await provider.history(PageRequest(limit=3), handle)
        assert history.items
        now = datetime.now(UTC)
        run = uuid.uuid4().hex
        provider_event_ids = tuple(
            hashlib.sha256(
                (
                    f"{item.ref.provider_content_id}:"
                    f"{cast('datetime', item.source_timestamp).isoformat()}"
                ).encode()
            ).hexdigest()
            for item in history.items
        )
        imported = tuple(
            ProviderHistoryImportObservation(
                observation_id=_observation_id(),
                idempotency_key=f"history:{provider_event_ids[index]}",
                occurred_at=cast("datetime", item.source_timestamp),
                received_at=now,
                account_id="bilibili-e2e",
                content_ref=item.ref,
                provenance=ObservationProvenance(
                    producer_id="provider.bilibili",
                    source=ObservationSource.PROVIDER_IMPORT,
                    authenticated=True,
                    trust_level=TrustLevel.HIGH,
                ),
                payload=HistoryImportPayload(
                    provider_event_id=provider_event_ids[index],
                    progress_seconds=None,
                ),
            )
            for index, item in enumerate(history.items)
        )
        result = await facade.record_observations(
            RecordObservationsCommand(
                idempotency_key=f"e2e:l2:history-batch:{run}",
                observations=imported,
                allowed_event_types=frozenset({"provider_history_import"}),
            )
        )
        assert result.items
        assert all(
            item.status in {RecordStatus.INSERTED, RecordStatus.DUPLICATE} for item in result.items
        )
        duplicate = await facade.record_observations(
            RecordObservationsCommand(
                idempotency_key=f"e2e:l2:history-retry:{run}",
                observations=imported,
                allowed_event_types=frozenset({"provider_history_import"}),
            )
        )
        assert all(item.status is RecordStatus.DUPLICATE for item in duplicate.items)
        observations = application.services.observations
        assert observations is not None
        replay = await observations.query(after_cursor=None, limit=500)
        stored_ids = frozenset(
            item.observation_id for item in result.items if item.observation_id is not None
        )
        replayed = tuple(item for item in replay.items if item.observation_id in stored_ids)
        assert len(replayed) == len(imported)
        assert all(item.event_type == "provider_history_import" for item in replayed)
        assert all(item.provenance.source is ObservationSource.PROVIDER_IMPORT for item in replayed)
        assert all(item.content_ref is not None for item in replayed)
    finally:
        await application.stop()
