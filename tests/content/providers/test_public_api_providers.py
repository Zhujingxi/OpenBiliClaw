from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import pytest
from pydantic import ValidationError

from openbiliclaw.access.models import AnonymousAccessHandle, Permission
from openbiliclaw.content.integration.capabilities import (
    ContentPage,
    FeedQuery,
    PageRequest,
    ProviderCursor,
    SearchCapability,
    SearchQuery,
)
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ProviderId
from openbiliclaw.content.integration.manifest import CapabilityKind
from openbiliclaw.content.integration.testing import validate_provider_contract
from openbiliclaw.content.providers.bangumi import BANGUMI_MANIFEST, BangumiClient, BangumiProvider
from openbiliclaw.content.providers.bangumi.models import BangumiPage
from openbiliclaw.content.providers.linuxdo import (
    LINUXDO_MANIFEST,
    LinuxDoClient,
    LinuxDoProvider,
)
from openbiliclaw.content.providers.linuxdo.models import LinuxDoItem, LinuxDoPage
from openbiliclaw.content.providers.v2ex import V2EX_MANIFEST, V2EXClient, V2EXProvider
from openbiliclaw.content.providers.v2ex.models import V2EXPage
from openbiliclaw.content.providers.youtube import YOUTUBE_MANIFEST, YouTubeClient, YouTubeProvider
from openbiliclaw.content.providers.youtube.models import YouTubePage

if TYPE_CHECKING:
    from openbiliclaw.content.integration.manifest import ProviderManifest
    from openbiliclaw.content.integration.projections import ContentPreview
    from openbiliclaw.core._pydantic import StrictBaseModel

NOW = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)


class SearchProvider(Protocol):
    async def search(
        self, query: SearchQuery, access: AnonymousAccessHandle
    ) -> ContentPage[ContentPreview]: ...


class PageClient(Protocol):
    async def page(self, operation: str, argument: str, cursor: str, limit: int) -> object: ...


class LinuxDoTransport:
    def __init__(self, search_payload: bytes, fetch_payload: bytes) -> None:
        self.search_payload = search_payload
        self.fetch_payload = fetch_payload

    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes:
        return self.search_payload

    async def fetch(self, content_id: str, credential: str | None) -> bytes:
        return self.fetch_payload


class Transport:
    def __init__(self, pages: dict[tuple[str, str], bytes]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str, str, int]] = []

    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes:
        self.calls.append((operation, argument, cursor, limit))
        return self.pages[(operation, argument)]


def access(provider: str) -> AnonymousAccessHandle:
    return AnonymousAccessHandle(
        provider_id=provider,
        account_id=None,
        permissions=frozenset({Permission.READ_PUBLIC}),
    )


def youtube_page(*, cursor: str | None = "p2") -> bytes:
    return (
        YouTubePage(
            items=(
                {
                    "id": "abcdefghijk",
                    "title": "Typed YouTube",
                    "description": "video summary",
                    "channel": {"id": "UC1234567890", "name": "Channel"},
                    "published_at": NOW,
                    "duration_seconds": 123,
                    "view_count": 10,
                    "thumbnail_url": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg",
                    "availability": "available",
                },
            ),
            next_cursor=cursor,
        )
        .model_dump_json()
        .encode()
    )


def bangumi_page(*, cursor: str | None = "20") -> bytes:
    return (
        BangumiPage(
            items=(
                {
                    "id": 42,
                    "subject_type": "anime",
                    "title": "Typed Anime",
                    "original_title": "Typed",
                    "summary": "anime summary",
                    "creator": "Director",
                    "published_at": NOW,
                    "image_url": "https://lain.bgm.tv/pic/cover/l/test.jpg",
                    "score": 8.5,
                    "rating_count": 20,
                    "collection_count": 30,
                    "availability": "available",
                },
            ),
            next_cursor=cursor,
        )
        .model_dump_json()
        .encode()
    )


def linuxdo_item() -> LinuxDoItem:
    return LinuxDoItem(
        id="42",
        title="Typed Linux.do Topic",
        body="topic body",
        author="alice",
        url="https://linux.do/t/topic/42",
        published_at=int(NOW.timestamp()),
        deleted=False,
    )


def linuxdo_page(*, cursor: str | None = "2") -> bytes:
    return LinuxDoPage(items=(linuxdo_item(),), next_cursor=cursor).model_dump_json().encode()


def v2ex_page(*, cursor: str | None = "20") -> bytes:
    return (
        V2EXPage(
            items=(
                {
                    "id": 99,
                    "title": "Typed Topic",
                    "content": "topic body",
                    "member": {"username": "alice"},
                    "node": {"name": "python", "title": "Python"},
                    "published_at": NOW,
                    "reply_count": 7,
                    "availability": "available",
                },
            ),
            next_cursor=cursor,
        )
        .model_dump_json()
        .encode()
    )


@pytest.mark.parametrize(
    ("manifest", "provider"),
    [
        (YOUTUBE_MANIFEST, YouTubeProvider(YouTubeClient(Transport({})))),
        (BANGUMI_MANIFEST, BangumiProvider(BangumiClient(Transport({})))),
        (
            LINUXDO_MANIFEST,
            LinuxDoProvider(
                LinuxDoClient(
                    LinuxDoTransport(linuxdo_page(), linuxdo_item().model_dump_json().encode())
                )
            ),
        ),
        (V2EX_MANIFEST, V2EXProvider(V2EXClient(Transport({})))),
    ],
)
def test_manifest_matches_provider(manifest: ProviderManifest, provider: object) -> None:
    assert validate_provider_contract(manifest, provider) == ()


def test_v2ex_does_not_advertise_unofficial_search() -> None:
    provider = V2EXProvider(V2EXClient(Transport({})))
    assert CapabilityKind.SEARCH not in V2EX_MANIFEST.capabilities
    assert not isinstance(provider, SearchCapability)


@pytest.mark.parametrize(
    ("page_type", "payload"),
    [
        (YouTubePage, youtube_page()),
        (BangumiPage, bangumi_page()),
        (LinuxDoPage, linuxdo_page()),
        (V2EXPage, v2ex_page()),
    ],
)
def test_native_pages_reject_schema_drift(page_type: type[StrictBaseModel], payload: bytes) -> None:
    import json

    value = json.loads(payload)
    value["unexpected"] = "drift"
    with pytest.raises(ValidationError):
        page_type.model_validate(value)


async def test_youtube_search_pagination_canonical_id_and_media_projection() -> None:
    transport = Transport(
        {
            ("search", "typed"): youtube_page(),
            ("fetch", "abcdefghijk"): youtube_page(cursor=None),
        }
    )
    provider = YouTubeProvider(YouTubeClient(transport))
    page = await provider.search(
        SearchQuery(text="typed", page=PageRequest(limit=5)), access("youtube")
    )
    assert page.items[0].ref.provider_content_id == "abcdefghijk"
    assert page.items[0].ref.canonical_url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert page.items[0].source_timestamp == NOW
    assert page.next_cursor == ProviderCursor(provider_id=ProviderId(value="youtube"), value="p2")
    native = await provider.fetch(page.items[0].ref, access("youtube"))
    card = provider.card_data(native)
    assert card.image_url == "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"
    assert "123s" in str(card.badge)


async def test_bangumi_feed_cursor_and_projection() -> None:
    transport = Transport(
        {("feed", "rank"): bangumi_page(), ("fetch", "42"): bangumi_page(cursor=None)}
    )
    provider = BangumiProvider(BangumiClient(transport))
    page = await provider.feed(
        FeedQuery(feed_id="rank", page=PageRequest(limit=3)), access("bangumi")
    )
    assert page.items[0].ref.canonical_url == "https://bgm.tv/subject/42"
    assert page.next_cursor == ProviderCursor(provider_id=ProviderId(value="bangumi"), value="20")
    native = await provider.fetch(page.items[0].ref, access("bangumi"))
    assert provider.card_data(native).badge == "Bangumi · 8.5"


async def test_linuxdo_search_canonical_topic_and_all_projections() -> None:
    provider = LinuxDoProvider(
        LinuxDoClient(LinuxDoTransport(linuxdo_page(), linuxdo_item().model_dump_json().encode()))
    )
    page = await provider.search(
        SearchQuery(text="typed", page=PageRequest(limit=20)), access("linuxdo")
    )
    assert page.items[0].ref.canonical_url == "https://linux.do/t/topic/42"
    assert page.next_cursor == ProviderCursor(provider_id=ProviderId(value="linuxdo"), value="2")
    native = await provider.fetch(page.items[0].ref, access("linuxdo"))
    assert provider.preview(native).ref == native.ref
    assert provider.recommendation_candidate(native).ref == native.ref
    assert provider.search_document(native).body == "topic body"
    assert provider.card_data(native).badge == "LinuxDo"


async def test_v2ex_feed_canonical_topic_and_published_time() -> None:
    transport = Transport({("feed", "hot"): v2ex_page(), ("fetch", "99"): v2ex_page(cursor=None)})
    provider = V2EXProvider(V2EXClient(transport))
    page = await provider.feed(FeedQuery(feed_id="hot"), access("v2ex"))
    assert page.items[0].ref.canonical_url == "https://www.v2ex.com/t/99"
    assert page.items[0].source_timestamp == NOW
    native = await provider.fetch(page.items[0].ref, access("v2ex"))
    assert provider.recommendation_candidate(native).ref == native.ref
    assert provider.search_document(native).body
    assert provider.card_data(native).badge == "V2EX · Python · 7 replies"


async def test_wrong_access_is_rejected() -> None:
    provider = YouTubeProvider(YouTubeClient(Transport({})))
    with pytest.raises(ContentIntegrationError) as exc:
        await provider.search(SearchQuery(text="x"), access("v2ex"))
    assert exc.value.code is IntegrationErrorCode.ACCESS_DENIED


@pytest.mark.parametrize(
    ("provider", "provider_id"),
    [
        (YouTubeProvider(YouTubeClient(Transport({}))), "youtube"),
        (BangumiProvider(BangumiClient(Transport({}))), "bangumi"),
        (
            LinuxDoProvider(
                LinuxDoClient(
                    LinuxDoTransport(linuxdo_page(), linuxdo_item().model_dump_json().encode())
                )
            ),
            "linuxdo",
        ),
    ],
)
async def test_foreign_cursor_is_rejected(provider: SearchProvider, provider_id: str) -> None:
    with pytest.raises(ContentIntegrationError) as exc:
        await provider.search(
            SearchQuery(
                text="x",
                page=PageRequest(
                    cursor=ProviderCursor(provider_id=ProviderId(value="other"), value="2")
                ),
            ),
            access(provider_id),
        )
    assert exc.value.code is IntegrationErrorCode.INVALID_CONTENT_REF


@pytest.mark.parametrize(
    "client",
    [
        YouTubeClient(Transport({("search", "x"): b"{}"})),
        BangumiClient(Transport({("search", "x"): b"{}"})),
        V2EXClient(Transport({("search", "x"): b"{}"})),
    ],
)
async def test_client_wraps_malformed_transport_payload(client: PageClient) -> None:
    with pytest.raises(ContentIntegrationError) as exc:
        await client.page("search", "x", "0", 1)
    assert exc.value.code is IntegrationErrorCode.PROVIDER_UNAVAILABLE
