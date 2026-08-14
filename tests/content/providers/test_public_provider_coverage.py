from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import pytest
from pydantic import TypeAdapter

from openbiliclaw.access.models import AccessHandle, AnonymousAccessHandle, Permission
from openbiliclaw.content.integration.capabilities import (
    ContentPage,
    CreatorCapability,
    CreatorQuery,
    PageRequest,
    SearchQuery,
)
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.providers.bangumi import BangumiClient, BangumiProvider
from openbiliclaw.content.providers.bangumi.models import BangumiPage, BangumiSubject
from openbiliclaw.content.providers.linuxdo import LinuxDoClient, LinuxDoProvider
from openbiliclaw.content.providers.linuxdo.models import LinuxDoItem, LinuxDoPage
from openbiliclaw.content.providers.v2ex import V2EXClient, V2EXProvider
from openbiliclaw.content.providers.v2ex.models import V2EXMember, V2EXNode, V2EXPage, V2EXTopic
from openbiliclaw.content.providers.youtube import YouTubeClient, YouTubeProvider
from openbiliclaw.content.providers.youtube.models import YouTubeChannel, YouTubePage, YouTubeVideo

if TYPE_CHECKING:
    from openbiliclaw.content.integration.native import NativeContent
    from openbiliclaw.content.integration.projections import (
        CardData,
        ContentPreview,
        RecommendationCandidate,
        SearchDocument,
    )

NOW = datetime(2025, 1, 1, tzinfo=UTC)
_PAYLOAD = TypeAdapter(dict[str, object])


class PageDump(Protocol):
    def model_dump_json(self) -> str: ...


class PublicProvider(Protocol):
    async def search(
        self, query: SearchQuery, access: AccessHandle
    ) -> ContentPage[ContentPreview]: ...
    async def fetch(self, ref: ContentRef, access: AccessHandle) -> NativeContent: ...
    def native_from_payload(self, payload: dict[str, object]) -> NativeContent: ...
    def preview(self, content: NativeContent) -> ContentPreview: ...
    def recommendation_candidate(self, content: NativeContent) -> RecommendationCandidate: ...
    def search_document(self, content: NativeContent) -> SearchDocument: ...
    def card_data(self, content: NativeContent) -> CardData: ...
    def _ref(self, ref: ContentRef) -> None: ...


class LinuxTransport:
    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes:
        return LinuxDoPage(items=(linux_topic(),), next_cursor=None).model_dump_json().encode()

    async def fetch(self, content_id: str, credential: str | None) -> bytes:
        return linux_topic().model_dump_json().encode()


class Transport:
    def __init__(self, page: PageDump) -> None:
        self.page = page

    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes:
        return self.page.model_dump_json().encode()


def access(provider: str) -> AnonymousAccessHandle:
    return AnonymousAccessHandle(
        provider_id=provider, account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )


def video() -> YouTubeVideo:
    return YouTubeVideo(
        id="abcdefghijk",
        title="video",
        description="body",
        channel=YouTubeChannel(id="UC1", name="channel"),
        published_at=NOW,
        duration_seconds=2,
        view_count=3,
        thumbnail_url=None,
        availability="available",
    )


def subject() -> BangumiSubject:
    return BangumiSubject(
        id=1,
        title="subject",
        summary="body",
        creator=None,
        published_at=NOW,
        subject_type="anime",
        original_title="",
        image_url=None,
        score=0,
        rating_count=0,
        collection_count=0,
        availability="tombstone",
    )


def linux_topic() -> LinuxDoItem:
    return LinuxDoItem(
        id="1",
        title="topic",
        body="body",
        author="a",
        url="https://linux.do/t/topic/1",
        published_at=int(NOW.timestamp()),
    )


def topic() -> V2EXTopic:
    return V2EXTopic(
        id=1,
        title="topic",
        content="body",
        member=V2EXMember(username="a"),
        published_at=NOW,
        node=V2EXNode(name="n", title="Node"),
        reply_count=0,
        availability="tombstone",
    )


@pytest.mark.parametrize(
    ("provider", "provider_id", "query"),
    [
        (
            YouTubeProvider(
                YouTubeClient(Transport(YouTubePage(items=(video(),), next_cursor=None)))
            ),
            "youtube",
            CreatorQuery(creator_id="UC1"),
        ),
        (
            V2EXProvider(V2EXClient(Transport(V2EXPage(items=(topic(),), next_cursor=None)))),
            "v2ex",
            CreatorQuery(creator_id="a"),
        ),
    ],
)
async def test_creator_capability(
    provider: CreatorCapability, provider_id: str, query: CreatorQuery
) -> None:
    result = await provider.creator(query, access(provider_id))
    assert len(result.items) == 1


@pytest.mark.parametrize(
    ("provider", "provider_id"),
    [
        (
            YouTubeProvider(
                YouTubeClient(Transport(YouTubePage(items=(video(),), next_cursor=None)))
            ),
            "youtube",
        ),
        (
            BangumiProvider(
                BangumiClient(Transport(BangumiPage(items=(subject(),), next_cursor=None)))
            ),
            "bangumi",
        ),
        (LinuxDoProvider(LinuxDoClient(LinuxTransport())), "linuxdo"),
    ],
)
async def test_search_and_all_projections(provider: PublicProvider, provider_id: str) -> None:
    result = await provider.search(
        SearchQuery(text="x", page=PageRequest(limit=1)), access(provider_id)
    )
    native = await provider.fetch(result.items[0].ref, access(provider_id))
    assert provider.recommendation_candidate(native).ref == native.ref
    assert provider.search_document(native).body
    assert provider.card_data(native).ref == native.ref


@pytest.mark.parametrize(
    ("provider", "provider_id", "kind", "item_id"),
    [
        (
            YouTubeProvider(YouTubeClient(Transport(YouTubePage(items=(), next_cursor=None)))),
            "youtube",
            "video",
            "abcdefghijk",
        ),
        (
            BangumiProvider(BangumiClient(Transport(BangumiPage(items=(), next_cursor=None)))),
            "bangumi",
            "subject",
            "1",
        ),
        (
            LinuxDoProvider(LinuxDoClient(LinuxTransport())),
            "linuxdo",
            "topic",
            "1",
        ),
        (
            V2EXProvider(V2EXClient(Transport(V2EXPage(items=(), next_cursor=None)))),
            "v2ex",
            "topic",
            "1",
        ),
    ],
)
def test_native_from_payload_and_wrong_payload_projection(
    provider: PublicProvider, provider_id: str, kind: str, item_id: str
) -> None:
    model: PageDump = {
        "youtube": video(),
        "bangumi": subject(),
        "linuxdo": linux_topic(),
        "v2ex": topic(),
    }[provider_id]
    native = provider.native_from_payload(_PAYLOAD.validate_json(model.model_dump_json()))
    assert native.ref.provider_content_id == item_id
    foreign = YouTubeProvider(
        YouTubeClient(Transport(YouTubePage(items=(), next_cursor=None)))
    ).native_from_model(video())
    if provider_id != "youtube":
        with pytest.raises(ValueError):
            provider.preview(foreign)


@pytest.mark.parametrize(
    ("provider", "provider_id", "kind"),
    [
        (
            YouTubeProvider(YouTubeClient(Transport(YouTubePage(items=(), next_cursor=None)))),
            "youtube",
            "video",
        ),
        (
            BangumiProvider(BangumiClient(Transport(BangumiPage(items=(), next_cursor=None)))),
            "bangumi",
            "subject",
        ),
        (LinuxDoProvider(LinuxDoClient(LinuxTransport())), "linuxdo", "topic"),
        (
            V2EXProvider(V2EXClient(Transport(V2EXPage(items=(), next_cursor=None)))),
            "v2ex",
            "topic",
        ),
    ],
)
def test_foreign_ref_rejected(provider: PublicProvider, provider_id: str, kind: str) -> None:
    ref = ContentRef(
        provider_id=ProviderId(value="other"),
        content_kind=ContentKind(value=kind),
        provider_content_id="1",
        canonical_url="https://example.com/1",
    )
    with pytest.raises(ContentIntegrationError) as exc:
        provider._ref(ref)
    assert exc.value.code is IntegrationErrorCode.INVALID_CONTENT_REF
