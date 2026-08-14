"""L1 Weibo: real anonymous visitor-flow acquisition through the production graph."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import pytest

from openbiliclaw.access.models import AccessRequest, Permission
from openbiliclaw.application.sources import ConnectSourceCommand
from openbiliclaw.composition.build import BuildOptions, build_application, validated_settings
from openbiliclaw.content.integration.identity import ProviderId

if TYPE_CHECKING:
    from openbiliclaw.composition.application import Application
    from openbiliclaw.content.integration.projections import ContentPreview

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l1weibo, pytest.mark.asyncio]
_ROOT = Path(__file__).resolve().parents[2]
_QUERY = "开源"


async def _application() -> Application:
    settings = validated_settings(_ROOT / "data-e2e" / "config.e2e.toml")
    application = build_application(settings, options=BuildOptions(data_dir=_ROOT / "data-e2e"))
    await application.start()
    assert application.services.facade is not None
    assert application.providers is not None
    await application.services.facade.connect_source(
        ConnectSourceCommand(
            idempotency_key=f"e2e:l1weibo:{uuid.uuid4().hex}",
            request=AccessRequest(
                provider_id="weibo",
                permissions=frozenset({Permission.READ_PUBLIC}),
                supported_method_ids=("builtin.anonymous",),
            ),
            allowed_method_ids=frozenset({"builtin.anonymous"}),
        )
    )
    return application


def _assert_preview_identity(items: tuple[ContentPreview, ...]) -> None:
    assert items, "Weibo returned no items; upstream blocking/error must be investigated"
    ids: list[str] = []
    for item in items:
        content_id = item.ref.provider_content_id
        parsed = urlparse(item.ref.canonical_url)
        assert item.ref.provider_id == ProviderId(value="weibo")
        assert content_id and content_id.isdecimal()
        assert parsed.scheme == "https" and parsed.netloc == "weibo.com"
        assert parsed.path.startswith("/status/") and parsed.path.removeprefix("/status/")
        assert item.title.strip()
        ids.append(content_id)
    assert len(ids) == len(set(ids))


async def test_real_weibo_search_identity_is_stable() -> None:
    application = await _application()
    try:
        facade = application.services.facade
        assert facade is not None
        first = await facade.search_content("weibo", _QUERY, 20)
        second = await facade.search_content("weibo", _QUERY, 20)
        _assert_preview_identity(first.items)
        _assert_preview_identity(second.items)
    finally:
        await application.stop()


async def test_real_weibo_search_results_carry_full_previews() -> None:
    """Weibo has no anonymous detail endpoint; previews must stand on their own."""
    application = await _application()
    try:
        facade = application.services.facade
        assert facade is not None
        search = await facade.search_content("weibo", _QUERY, 20)
        _assert_preview_identity(search.items)
        for item in search.items:
            assert item.source_timestamp is not None
            assert item.summary.strip()
    finally:
        await application.stop()
