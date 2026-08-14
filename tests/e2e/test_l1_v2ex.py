"""L1 V2EX: real anonymous acquisition through the official public API."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from openbiliclaw.access.models import AccessHandle, AccessRequest, Permission
from openbiliclaw.application.sources import ConnectSourceCommand
from openbiliclaw.composition.build import BuildOptions, build_application, validated_settings
from openbiliclaw.content.integration.capabilities import FeedCapability, FeedQuery, PageRequest
from openbiliclaw.content.integration.identity import ProviderId
from openbiliclaw.content.providers.v2ex.models import V2EXTopic

if TYPE_CHECKING:
    from openbiliclaw.composition.application import Application
    from openbiliclaw.composition.facade import CompositionFacade
    from openbiliclaw.content.integration.projections import ContentPreview

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l1v2ex, pytest.mark.asyncio]
_ROOT = Path(__file__).resolve().parents[2]


async def _application() -> Application:
    settings = validated_settings(_ROOT / "data-e2e" / "config.e2e.toml")
    application = build_application(settings, options=BuildOptions(data_dir=_ROOT / "data-e2e"))
    await application.start()
    assert application.services.facade is not None
    assert application.providers is not None
    await application.services.facade.connect_source(
        ConnectSourceCommand(
            idempotency_key=f"e2e:l1v2ex:{uuid.uuid4().hex}",
            request=AccessRequest(
                provider_id="v2ex",
                permissions=frozenset({Permission.READ_PUBLIC}),
                supported_method_ids=("builtin.anonymous",),
            ),
            allowed_method_ids=frozenset({"builtin.anonymous"}),
        )
    )
    return application


def _assert_preview_identity(items: tuple[ContentPreview, ...]) -> None:
    assert items, "V2EX returned no hot topics; upstream blocking/error must be investigated"
    ids: list[str] = []
    for item in items:
        content_id = item.ref.provider_content_id
        assert item.ref.provider_id == ProviderId(value="v2ex")
        assert content_id.isdecimal()
        assert item.ref.canonical_url == f"https://www.v2ex.com/t/{content_id}"
        assert item.title.strip()
        ids.append(content_id)
    assert len(ids) == len(set(ids))


def _feed(application: Application) -> tuple[FeedCapability, AccessHandle]:
    assert application.providers is not None
    facade = application.services.facade
    assert facade is not None
    provider = application.providers.registry.provider(ProviderId(value="v2ex"))
    assert isinstance(provider, FeedCapability)
    handle = cast("CompositionFacade", facade)._access.connected_handle(  # noqa: SLF001
        "v2ex", None
    )
    assert handle is not None
    return provider, handle


async def test_real_v2ex_hot_feed_identity_is_stable() -> None:
    application = await _application()
    try:
        provider, handle = _feed(application)
        query = FeedQuery(feed_id="hot", page=PageRequest(limit=20))
        first = await provider.feed(query, handle)
        second = await provider.feed(query, handle)
        _assert_preview_identity(first.items)
        _assert_preview_identity(second.items)
    finally:
        await application.stop()


async def test_real_v2ex_hot_topic_fetches_detail() -> None:
    application = await _application()
    try:
        facade = application.services.facade
        assert facade is not None
        provider, handle = _feed(application)
        feed = await provider.feed(FeedQuery(feed_id="hot", page=PageRequest(limit=20)), handle)
        _assert_preview_identity(feed.items)

        result = await facade.get_content_details(feed.items[0].ref.model_dump_json())
        assert result.content.ref == feed.items[0].ref
        assert isinstance(result.content.payload, V2EXTopic)
        assert result.content.payload.title.strip()
    finally:
        await application.stop()
