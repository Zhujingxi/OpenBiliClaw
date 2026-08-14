"""L1 YouTube: real anonymous yt-dlp acquisition through the production graph."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from openbiliclaw.access.models import AccessRequest, Permission
from openbiliclaw.application.sources import ConnectSourceCommand
from openbiliclaw.composition.build import BuildOptions, build_application, validated_settings
from openbiliclaw.content.integration.capabilities import (
    CreatorCapability,
    CreatorQuery,
    PageRequest,
)
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.providers.youtube.capabilities import YouTubeProvider
from openbiliclaw.content.providers.youtube.models import YouTubeVideo

if TYPE_CHECKING:
    from openbiliclaw.composition.application import Application
    from openbiliclaw.composition.facade import CompositionFacade
    from openbiliclaw.content.integration.projections import ContentPreview

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_l1youtube, pytest.mark.asyncio]
_ROOT = Path(__file__).resolve().parents[2]
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_STABLE_VIDEO_ID = "dQw4w9WgXcQ"
_STABLE_CHANNEL_ID = "UCuAXFkgsw1L7xaCfnd5JJOw"


async def _application() -> Application:
    settings = validated_settings(_ROOT / "data-e2e" / "config.e2e.toml")
    application = build_application(settings, options=BuildOptions(data_dir=_ROOT / "data-e2e"))
    await application.start()
    assert application.services.facade is not None
    assert application.providers is not None
    await application.services.facade.connect_source(
        ConnectSourceCommand(
            idempotency_key=f"e2e:l1youtube:{uuid.uuid4().hex}",
            request=AccessRequest(
                provider_id="youtube",
                permissions=frozenset({Permission.READ_PUBLIC}),
                supported_method_ids=("builtin.anonymous",),
            ),
            allowed_method_ids=frozenset({"builtin.anonymous"}),
        )
    )
    return application


def _assert_preview_identity(items: tuple[ContentPreview, ...]) -> None:
    assert items, "YouTube returned no items; upstream blocking/error must be investigated"
    ids: list[str] = []
    for item in items:
        assert item.ref.provider_id == ProviderId(value="youtube")
        assert _VIDEO_ID.fullmatch(item.ref.provider_content_id)
        assert item.ref.canonical_url == (
            f"https://www.youtube.com/watch?v={item.ref.provider_content_id}"
        )
        assert item.title.strip()
        ids.append(item.ref.provider_content_id)
    assert len(ids) == len(set(ids))


async def test_real_youtube_search_identity_is_stable() -> None:
    application = await _application()
    try:
        facade = application.services.facade
        assert facade is not None
        first = await facade.search_content("youtube", "python tutorial", 3)
        second = await facade.search_content("youtube", "python tutorial", 3)
        _assert_preview_identity(first.items)
        _assert_preview_identity(second.items)
    finally:
        await application.stop()


async def test_real_youtube_fetch_creator_page_cap_and_typed_invalid_ref() -> None:
    application = await _application()
    try:
        facade = application.services.facade
        assert facade is not None
        assert application.providers is not None
        provider = application.providers.registry.provider(ProviderId(value="youtube"))
        assert isinstance(provider, YouTubeProvider)
        assert isinstance(provider, CreatorCapability)
        handle = cast("CompositionFacade", facade)._access.connected_handle(  # noqa: SLF001
            "youtube", None
        )
        assert handle is not None

        stable_ref = ContentRef(
            provider_id=ProviderId(value="youtube"),
            content_kind=ContentKind(value="video"),
            provider_content_id=_STABLE_VIDEO_ID,
            canonical_url=f"https://www.youtube.com/watch?v={_STABLE_VIDEO_ID}",
        )
        native = await provider.fetch(stable_ref, handle)
        assert isinstance(native.payload, YouTubeVideo)
        assert native.payload.title.strip()
        assert native.payload.channel is not None
        assert native.payload.channel.name.strip()

        creator = await provider.creator(
            CreatorQuery(creator_id=_STABLE_CHANNEL_ID, page=PageRequest(limit=3)), handle
        )
        _assert_preview_identity(creator.items)

        capped = await provider.creator(
            CreatorQuery(creator_id=_STABLE_CHANNEL_ID, page=PageRequest(limit=100)), handle
        )
        assert 1 <= len(capped.items) <= 50

        malformed = stable_ref.model_copy(update={"provider_content_id": "bad"})
        with pytest.raises(ContentIntegrationError) as raised:
            await provider.fetch(malformed, handle)
        assert raised.value.code is IntegrationErrorCode.INVALID_CONTENT_REF
    finally:
        await application.stop()
