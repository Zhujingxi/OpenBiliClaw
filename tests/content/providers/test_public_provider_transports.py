from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.providers.bangumi.client import HttpxBangumiTransport
from openbiliclaw.content.providers.v2ex.client import HttpxV2EXTransport

NOW = datetime(2025, 1, 2, tzinfo=UTC)


async def test_bangumi_transport_maps_search_and_endpoint() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v0/search/subjects"
        assert request.url.params["offset"] == "20"
        assert json.loads(request.content)["keyword"] == "typed"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 42,
                        "type": 2,
                        "name": "Typed",
                        "name_cn": "Typed Anime",
                        "summary": "summary",
                        "date": "2025-01-02",
                        "images": {"large": "https://img.example/42.jpg"},
                        "rating": {"score": 8.5, "total": 20},
                        "collection_total": 30,
                    }
                ],
                "total": 100,
                "limit": 5,
                "offset": 20,
            },
        )

    transport = HttpxBangumiTransport(httpx.MockTransport(handler))
    raw = await transport("search", "typed", "20", 5)
    page = json.loads(raw)
    assert page["items"][0]["id"] == 42
    assert page["items"][0]["subject_type"] == "anime"
    assert page["next_cursor"] == "25"
    assert transport.open_client_count == 0


async def test_bangumi_transport_tolerates_empty_image_urls() -> None:
    """Upstream rows may carry empty-string image URLs; they normalize to None."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 504554,
                        "type": 2,
                        "name": "Samac",
                        "name_cn": "孤独",
                        "summary": "summary",
                        "date": "1959-01-01",
                        "images": {
                            "small": "",
                            "grid": "",
                            "large": "",
                            "medium": "",
                            "common": "",
                        },
                        "rating": {"score": 0, "total": 2},
                        "collection_total": 4,
                    }
                ],
                "total": 1,
                "limit": 20,
                "offset": 0,
            },
        )

    transport = HttpxBangumiTransport(httpx.MockTransport(handler))
    raw = await transport("search", "typed", "0", 20)
    page = json.loads(raw)
    assert page["items"][0]["id"] == 504554
    assert page["items"][0]["image_url"] is None


async def test_v2ex_transport_maps_hot_topics_and_endpoint() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/topics/hot.json"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 99,
                    "title": "Typed topic",
                    "content": "body",
                    "created": 1735776000,
                    "replies": 7,
                    "member": {"username": "alice"},
                    "node": {"name": "python", "title": "Python"},
                }
            ],
        )

    transport = HttpxV2EXTransport(httpx.MockTransport(handler))
    raw = await transport("feed", "hot", "0", 5)
    page = json.loads(raw)
    assert page["items"][0]["id"] == 99
    assert page["items"][0]["published_at"] == "2025-01-02T00:00:00Z"
    assert page["next_cursor"] is None
    assert transport.open_client_count == 0


async def _assert_rate_limited(
    transport: HttpxBangumiTransport | HttpxV2EXTransport,
) -> None:
    with pytest.raises(ContentIntegrationError) as exc:
        await transport("feed", "hot", "0", 1)
    assert exc.value.code is IntegrationErrorCode.RATE_LIMITED
    assert "secret response body" not in str(exc.value)


async def test_http_transports_classify_rate_limit() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"secret response body")

    await _assert_rate_limited(HttpxBangumiTransport(httpx.MockTransport(handler)))
    await _assert_rate_limited(HttpxV2EXTransport(httpx.MockTransport(handler)))
