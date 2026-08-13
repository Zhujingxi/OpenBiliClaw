"""L1a: real anonymous Bilibili acquisition through the production graph."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from openbiliclaw.access.models import AccessRequest, Permission
from openbiliclaw.application.sources import ConnectSourceCommand
from openbiliclaw.composition.build import BuildOptions, build_application, validated_settings
from openbiliclaw.content.integration.capabilities import FeedCapability, FeedQuery, PageRequest
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ProviderId
from openbiliclaw.content.providers.bilibili.capabilities import BilibiliProvider
from openbiliclaw.content.providers.bilibili.client import BilibiliClient, HttpxBilibiliTransport
from openbiliclaw.content.providers.bilibili.models import BilibiliVideo

if TYPE_CHECKING:
    from openbiliclaw.composition.application import Application
    from openbiliclaw.composition.facade import CompositionFacade

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l1a, pytest.mark.asyncio]
_ROOT = Path(__file__).resolve().parents[2]
_BVID = re.compile(r"^BV[A-Za-z0-9]{10}$")


async def _application() -> Application:
    settings = validated_settings(_ROOT / "data-e2e" / "config.e2e.toml")
    application = build_application(settings, options=BuildOptions(data_dir=_ROOT / "data-e2e"))
    await application.start()
    assert application.services.facade is not None
    assert application.providers is not None
    await application.services.facade.connect_source(
        ConnectSourceCommand(
            # Fresh graphs need fresh keys until L1b fixes restart restoration;
            # see the L1a testing-log finding.
            idempotency_key=f"e2e:l1a:{uuid.uuid4().hex}",
            request=AccessRequest(
                provider_id="bilibili",
                permissions=frozenset({Permission.READ_PUBLIC}),
                supported_method_ids=("builtin.anonymous",),
            ),
            allowed_method_ids=frozenset({"builtin.anonymous"}),
        )
    )
    return application


def _assert_video(content: object) -> None:
    assert isinstance(content, BilibiliVideo)
    assert _BVID.fullmatch(content.id)
    assert content.title.strip()
    assert content.duration_seconds > 0


async def test_real_popular_search_identity_and_detail_are_stable() -> None:
    application = await _application()
    try:
        facade = application.services.facade
        assert facade is not None
        assert application.providers is not None
        provider = application.providers.registry.provider(ProviderId(value="bilibili"))
        assert isinstance(provider, FeedCapability)
        handle = cast("CompositionFacade", facade)._access.connected_handle(  # noqa: SLF001
            "bilibili", None
        )
        assert handle is not None

        popular = await provider.feed(FeedQuery(page=PageRequest(limit=3)), handle)
        assert popular.items
        popular_detail = await facade.get_content_details(popular.items[0].ref.model_dump_json())
        _assert_video(popular_detail.content.payload)

        first = await facade.search_content("bilibili", "Python", 3)
        second = await facade.search_content("bilibili", "Python", 3)
        assert first.items and second.items
        for item in (*first.items, *second.items):
            assert _BVID.fullmatch(item.ref.provider_content_id)
            assert item.title.strip()
        repeated_detail = await facade.get_content_details(first.items[0].ref.model_dump_json())
        search_detail = await facade.get_content_details(first.items[0].ref.model_dump_json())
        assert repeated_detail.content.ref == search_detail.content.ref
        _assert_video(search_detail.content.payload)
        assert search_detail.content.ref == first.items[0].ref
    finally:
        await application.stop()


async def test_page_cap_and_unreachable_provider_failure_are_typed() -> None:
    application = await _application()
    try:
        facade = application.services.facade
        assert facade is not None
        assert application.providers is not None
        provider = application.providers.registry.provider(ProviderId(value="bilibili"))
        assert isinstance(provider, FeedCapability)
        handle = cast("CompositionFacade", facade)._access.connected_handle(  # noqa: SLF001
            "bilibili", None
        )
        assert handle is not None
        page = await provider.feed(FeedQuery(page=PageRequest(limit=100)), handle)
        assert 1 <= len(page.items) <= 50
    finally:
        await application.stop()

    async def resolve(_handle):  # type: ignore[no-untyped-def]
        raise AssertionError("anonymous request resolved credentials")

    dead = BilibiliProvider(
        BilibiliClient(HttpxBilibiliTransport(base_url="http://127.0.0.1:1"), resolve)
    )
    from openbiliclaw.access.models import AnonymousAccessHandle
    from openbiliclaw.content.integration.capabilities import SearchQuery

    access = AnonymousAccessHandle(
        provider_id="bilibili", account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )
    with pytest.raises(ContentIntegrationError) as raised:
        await dead.search(SearchQuery(text="Python", page=PageRequest(limit=1)), access)
    assert raised.value.code is IntegrationErrorCode.NETWORK_UNAVAILABLE
