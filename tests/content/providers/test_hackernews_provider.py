from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from openbiliclaw.access.models import AnonymousAccessHandle, Permission
from openbiliclaw.content.integration.capabilities import FeedQuery, PageRequest, ProviderCursor
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.testing import validate_provider_contract
from openbiliclaw.content.providers.hackernews import (
    HACKER_NEWS_MANIFEST,
    HackerNewsClient,
    HackerNewsProvider,
    HttpxHackerNewsTransport,
)
from openbiliclaw.content.providers.hackernews.models import HackerNewsItem, HackerNewsPage

NOW = datetime(2025, 1, 2, tzinfo=UTC)


def access(provider_id: str = "hackernews") -> AnonymousAccessHandle:
    return AnonymousAccessHandle(
        provider_id=provider_id,
        account_id=None,
        permissions=frozenset({Permission.READ_PUBLIC}),
    )


def item(item_id: int = 101) -> HackerNewsItem:
    return HackerNewsItem(
        id=item_id,
        item_type="story",
        title="Typed Hacker News",
        body="A story body",
        author="alice",
        published_at=NOW,
        score=42,
        comment_count=7,
        external_url="https://example.com/story",
    )


class StaticTransport:
    def __init__(self, page: HackerNewsPage) -> None:
        self.page = page

    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes:
        return self.page.model_dump_json().encode()


class MalformedTransport:
    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes:
        return b"{}"


def test_manifest_matches_provider_contract() -> None:
    provider = HackerNewsProvider(HackerNewsClient(StaticTransport(HackerNewsPage(items=()))))
    assert validate_provider_contract(HACKER_NEWS_MANIFEST, provider) == ()


async def test_official_transport_feed_fetch_and_projections() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/topstories.json":
            return httpx.Response(200, json=[101, 102, 103])
        if request.url.path == "/v0/item/101.json":
            return httpx.Response(
                200,
                json={
                    "id": 101,
                    "type": "story",
                    "by": "alice",
                    "time": int(NOW.timestamp()),
                    "title": "Typed &amp; safe",
                    "text": "<p>A <b>story</b> body</p><script>bad()</script><p>Second</p>",
                    "url": "https://example.com/story",
                    "score": 42,
                    "descendants": 7,
                    "extra": "ignored",
                },
            )
        if request.url.path == "/v0/item/102.json":
            return httpx.Response(200, json={"id": 102, "deleted": True})
        if request.url.path == "/v0/item/103.json":
            return httpx.Response(
                200,
                json={
                    "id": 103,
                    "type": "job",
                    "by": "bob",
                    "time": int(NOW.timestamp()),
                    "title": "A job",
                    "score": 1,
                },
            )
        raise AssertionError(request.url)

    transport = HttpxHackerNewsTransport(httpx.MockTransport(handler))
    provider = HackerNewsProvider(HackerNewsClient(transport))
    page = await provider.feed(FeedQuery(feed_id="top", page=PageRequest(limit=2)), access())

    assert len(page.items) == 1
    assert page.items[0].title == "Typed & safe"
    assert page.items[0].summary == "A story body Second"
    assert page.items[0].ref.canonical_url == "https://news.ycombinator.com/item?id=101"
    assert page.next_cursor == ProviderCursor(provider_id=ProviderId(value="hackernews"), value="2")

    native = await provider.fetch(page.items[0].ref, access())
    assert provider.preview(native).creator_label == "alice"
    assert provider.recommendation_candidate(native).discovery_reason == "hackernews:top"
    assert provider.search_document(native).body == "A story body Second"
    assert provider.card_data(native).badge == "Hacker News · 42 points · 7 comments"
    assert isinstance(native.payload, HackerNewsItem)
    assert native.payload.external_url == "https://example.com/story"
    assert transport.open_client_count == 0


async def test_transport_paginates_story_ids_not_skipped_items() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/topstories.json":
            return httpx.Response(200, json=[101, 102])
        return httpx.Response(
            200,
            json={
                "id": 102,
                "type": "poll",
                "time": int(NOW.timestamp()),
                "title": "Poll",
                "score": 0,
                "url": "javascript:ignored",
            },
        )

    provider = HackerNewsProvider(
        HackerNewsClient(HttpxHackerNewsTransport(httpx.MockTransport(handler)))
    )
    page = await provider.feed(
        FeedQuery(
            page=PageRequest(
                limit=1,
                cursor=ProviderCursor(provider_id=ProviderId(value="hackernews"), value="1"),
            )
        ),
        access(),
    )
    assert page.items[0].ref.provider_content_id == "102"
    assert page.next_cursor is None


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (403, IntegrationErrorCode.ACCESS_DENIED),
        (429, IntegrationErrorCode.RATE_LIMITED),
        (500, IntegrationErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
async def test_transport_classifies_safe_http_failures(
    status: int, code: IntegrationErrorCode
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"SECRET-CANARY")

    transport = HttpxHackerNewsTransport(httpx.MockTransport(handler))
    with pytest.raises(ContentIntegrationError) as exc:
        await transport("feed", "top", "0", 1)
    assert exc.value.code is code
    assert "SECRET-CANARY" not in str(exc.value)
    assert transport.open_client_count == 0


async def test_transport_and_client_reject_malformed_responses() -> None:
    async def invalid_ids(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[True])

    with pytest.raises(ContentIntegrationError) as ids_error:
        await HttpxHackerNewsTransport(httpx.MockTransport(invalid_ids))("feed", "top", "0", 1)
    assert ids_error.value.code is IntegrationErrorCode.PROVIDER_UNAVAILABLE

    client = HackerNewsClient(MalformedTransport())
    with pytest.raises(ContentIntegrationError) as payload_error:
        await client.page("feed", "top", "0", 1)
    assert payload_error.value.code is IntegrationErrorCode.PROVIDER_UNAVAILABLE


async def test_provider_rejects_foreign_access_cursor_ref_and_feed() -> None:
    provider = HackerNewsProvider(
        HackerNewsClient(StaticTransport(HackerNewsPage(items=(item(),), next_cursor=None)))
    )
    with pytest.raises(ContentIntegrationError) as access_error:
        await provider.feed(FeedQuery(), access("other"))
    assert access_error.value.code is IntegrationErrorCode.ACCESS_DENIED

    with pytest.raises(ContentIntegrationError) as cursor_error:
        await provider.feed(
            FeedQuery(
                page=PageRequest(
                    cursor=ProviderCursor(provider_id=ProviderId(value="other"), value="1")
                )
            ),
            access(),
        )
    assert cursor_error.value.code is IntegrationErrorCode.INVALID_CONTENT_REF

    with pytest.raises(ContentIntegrationError) as feed_error:
        await provider.feed(FeedQuery(feed_id="new"), access())
    assert feed_error.value.code is IntegrationErrorCode.INVALID_CONTENT_REF

    foreign = ContentRef(
        provider_id=ProviderId(value="other"),
        content_kind=ContentKind(value="item"),
        provider_content_id="101",
        canonical_url="https://example.com/101",
    )
    with pytest.raises(ContentIntegrationError) as ref_error:
        await provider.fetch(foreign, access())
    assert ref_error.value.code is IntegrationErrorCode.INVALID_CONTENT_REF

    malformed = ContentRef(
        provider_id=ProviderId(value="hackernews"),
        content_kind=ContentKind(value="item"),
        provider_content_id="../topstories",
        canonical_url="https://news.ycombinator.com/item?id=bad",
    )
    with pytest.raises(ContentIntegrationError) as malformed_error:
        await provider.fetch(malformed, access())
    assert malformed_error.value.code is IntegrationErrorCode.INVALID_CONTENT_REF


def test_native_page_rejects_schema_drift() -> None:
    value = json.loads(HackerNewsPage(items=(item(),)).model_dump_json())
    value["unexpected"] = True
    with pytest.raises(ValidationError):
        HackerNewsPage.model_validate(value)
