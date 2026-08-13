"""L1b: authenticated Bilibili acquisition with memory-only Chrome cookies."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from openbiliclaw.access.models import AccessStatusKind, CredentialAccessHandle
from openbiliclaw.composition.build import BuildOptions, build_application, validated_settings
from openbiliclaw.content.integration.capabilities import (
    PageRequest,
    RelatedCapability,
    SearchQuery,
)
from openbiliclaw.content.integration.identity import ProviderId
from openbiliclaw.content.providers.bilibili.capabilities import BilibiliProvider
from openbiliclaw.content.providers.bilibili.client import BilibiliClient, HttpxBilibiliTransport

from .bilibili_chrome import BrowserCookies, connect_command, extract_bilibili_cookies

if TYPE_CHECKING:
    from openbiliclaw.composition.application import Application
    from openbiliclaw.composition.facade import CompositionFacade

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l1b, pytest.mark.asyncio]
_ROOT = Path(__file__).resolve().parents[2]


class _CookieResolver:
    def __init__(self, cookies: BrowserCookies) -> None:
        self._cookies = cookies

    async def __call__(self, handle: CredentialAccessHandle) -> str:
        del handle
        return self._cookies.header


async def _application(cookies: BrowserCookies, key: str) -> Application:
    settings = validated_settings(_ROOT / "data-e2e" / "config.e2e.toml")
    application = build_application(settings, options=BuildOptions(data_dir=_ROOT / "data-e2e"))
    await application.start()
    facade = application.services.facade
    assert facade is not None
    result = await facade.connect_source(connect_command(cookies, key))
    assert result.status.state is AccessStatusKind.CONNECTED
    return application


def _handle(application: Application) -> CredentialAccessHandle:
    facade = application.services.facade
    assert facade is not None
    handle = cast("CompositionFacade", facade)._access.connected_handle(  # noqa: SLF001
        "bilibili", None
    )
    assert isinstance(handle, CredentialAccessHandle)
    return handle


async def test_authenticated_status_history_related_and_nav_identity() -> None:
    cookies = extract_bilibili_cookies()
    application = await _application(cookies, f"e2e:l1b:{uuid.uuid4().hex}")
    try:
        facade = application.services.facade
        assert facade is not None
        status = await facade.source_status("bilibili", None)
        assert status.status.state is AccessStatusKind.CONNECTED
        verification = status.status.verification
        assert verification is not None and verification.safe_account_identity
        nav = await BilibiliClient(
            HttpxBilibiliTransport(), _CookieResolver(cookies)
        ).nav_with_cookie(cookies.header)
        assert nav.is_login and int(nav.mid) > 0 and nav.name.strip()

        assert application.providers is not None
        provider = application.providers.registry.provider(ProviderId(value="bilibili"))
        assert isinstance(provider, BilibiliProvider)
        handle = _handle(application)
        history = await provider.history(PageRequest(limit=3), handle)
        for item in history.items:
            assert item.ref.provider_content_id.startswith("BV")
            assert item.title.strip()

        public = await provider.search(
            SearchQuery(text="Python", page=PageRequest(limit=1)), handle
        )
        assert public.items
        assert isinstance(provider, RelatedCapability)
        related = await provider.related(public.items[0].ref, PageRequest(limit=2), handle)
        for item in related.items:
            assert isinstance(item.title, str) and item.title.strip()
    finally:
        await application.stop()


async def test_restart_cached_key_resubmission_restores_usable_connection() -> None:
    cookies = extract_bilibili_cookies()
    key = f"e2e:l1b:restart:{uuid.uuid4().hex}"
    first = await _application(cookies, key)
    await first.stop()

    restarted = await _application(cookies, key)
    try:
        facade = restarted.services.facade
        assert facade is not None
        handle = _handle(restarted)
        assert handle.revision == 1
        result = await facade.search_content("bilibili", "Python", 1)
        assert result.items
        assert isinstance(result.items[0].title, str) and result.items[0].title.strip()
    finally:
        await restarted.stop()
