from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from pydantic import JsonValue

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.providers.bangumi.client import HttpxBangumiTransport
from openbiliclaw.content.providers.v2ex.client import HttpxV2EXTransport
from openbiliclaw.content.providers.youtube.client import (
    HttpxYouTubeTransport,
    _channel,
    _count,
    _duration,
    _published,
    _renderer_video,
    _thumbnail,
)


def _bangumi_subject() -> dict[str, object]:
    return {
        "id": 1,
        "type": 2,
        "name": "Name",
        "name_cn": "",
        "summary": "",
        "date": "bad-date",
        "images": {"common": "https://img.example/1.jpg"},
        "rating": {"score": 1, "total": 2},
        "collection_total": 3,
    }


def _v2ex_topic(topic_id: int = 1) -> dict[str, object]:
    return {
        "id": topic_id,
        "title": "Topic",
        "content_rendered": "body",
        "created": 0,
        "replies": 0,
        "member": {"username": "alice"},
        "node": {"name": "n", "title": "Node"},
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3, 3),
        ({"simpleText": "1.2K views"}, 1200),
        ({"simpleText": "none"}, 0),
    ],
)
def test_youtube_count_shapes(value: JsonValue, expected: int) -> None:
    assert _count(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"simpleText": "12"}, 12),
        ({"simpleText": "1:02:03"}, 3723),
        ({"simpleText": "bad"}, 0),
        ({"simpleText": "1:2:3:4"}, 0),
    ],
)
def test_youtube_duration_shapes(value: JsonValue, expected: int) -> None:
    assert _duration(value) == expected


def test_youtube_helper_fallbacks_and_invalid_renderer() -> None:
    assert _published(None) == datetime(1970, 1, 1, tzinfo=UTC)
    assert _thumbnail(None) is None
    assert _thumbnail({"thumbnails": [{"url": "not-a-url"}]}) is None
    assert _channel({}) is None
    assert _renderer_video({"videoId": "bad", "title": "x"}) is None


async def test_youtube_continuation_feed_and_invalid_envelope() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "continuationItemRenderer": {
                        "continuationEndpoint": {"continuationCommand": {"token": "next"}}
                    }
                },
            )
        return httpx.Response(200, json=[])

    transport = HttpxYouTubeTransport(httpx.MockTransport(handler))
    page = json.loads(await transport("feed", "trending", "0", 0))
    assert page["next_cursor"] == "next"
    assert json.loads(requests[0].content)["browseId"] == "FEtrending"
    with pytest.raises(ContentIntegrationError) as exc:
        await transport("creator", "UC1", "next", 1)
    assert exc.value.code is IntegrationErrorCode.PROVIDER_UNAVAILABLE
    assert json.loads(requests[1].content)["continuation"] == "next"


@pytest.mark.parametrize("status", [401, 500])
async def test_youtube_http_statuses(status: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(ContentIntegrationError) as exc:
        await HttpxYouTubeTransport(httpx.MockTransport(handler))("feed", "hot", "0", 1)
    expected = (
        IntegrationErrorCode.ACCESS_DENIED
        if status == 401
        else IntegrationErrorCode.PROVIDER_UNAVAILABLE
    )
    assert exc.value.code is expected


async def test_youtube_transport_error_is_normalized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret", request=request)

    with pytest.raises(ContentIntegrationError) as exc:
        await HttpxYouTubeTransport(httpx.MockTransport(handler))("feed", "hot", "0", 1)
    assert exc.value.code is IntegrationErrorCode.PROVIDER_UNAVAILABLE


async def test_bangumi_fetch_feed_invalid_type_and_bad_envelope() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/1"):
            return httpx.Response(200, json=_bangumi_subject())
        if len(calls) == 2:
            return httpx.Response(200, json={"data": [_bangumi_subject()], "total": 1})
        if len(calls) == 3:
            bad = _bangumi_subject()
            bad["type"] = 99
            return httpx.Response(200, json={"data": [bad], "total": 1})
        return httpx.Response(200, json=[])

    transport = HttpxBangumiTransport(httpx.MockTransport(handler))
    assert json.loads(await transport("fetch", "1", "0", 1))["items"][0]["id"] == 1
    assert (
        json.loads(await transport("feed", "date", "0", 1))["items"][0]["original_title"] == "Name"
    )
    with pytest.raises(ContentIntegrationError):
        await transport("feed", "rank", "0", 1)
    with pytest.raises(ContentIntegrationError):
        await transport("feed", "rank", "0", 1)


@pytest.mark.parametrize("status", [401, 500])
async def test_bangumi_http_statuses(status: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(ContentIntegrationError) as exc:
        await HttpxBangumiTransport(httpx.MockTransport(handler))("feed", "rank", "0", 1)
    assert exc.value.code is (
        IntegrationErrorCode.ACCESS_DENIED
        if status == 401
        else IntegrationErrorCode.PROVIDER_UNAVAILABLE
    )


async def test_bangumi_transport_error_is_normalized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret", request=request)

    with pytest.raises(ContentIntegrationError):
        await HttpxBangumiTransport(httpx.MockTransport(handler))("feed", "rank", "0", 1)


async def test_v2ex_fetch_creator_latest_pagination_and_object_response() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/topics/latest.json":
            return httpx.Response(200, json=[_v2ex_topic(1), _v2ex_topic(2)])
        return httpx.Response(200, json=_v2ex_topic())

    transport = HttpxV2EXTransport(httpx.MockTransport(handler))
    assert json.loads(await transport("fetch", "1", "0", 1))["items"][0]["id"] == 1
    assert calls[-1].url.params["id"] == "1"
    await transport("creator", "alice", "0", 1)
    assert calls[-1].url.params["username"] == "alice"
    page = json.loads(await transport("feed", "latest", "0", 1))
    assert page["next_cursor"] == "1"
    searched = json.loads(await transport("search", "topic", "0", 5))
    assert len(searched["items"]) == 1
    empty = json.loads(await transport("search", "missing", "0", 5))
    assert empty["items"] == []


async def test_v2ex_invalid_envelope_and_transport_error() -> None:
    async def invalid(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="wrong")

    with pytest.raises(ContentIntegrationError):
        await HttpxV2EXTransport(httpx.MockTransport(invalid))("feed", "hot", "0", 1)

    async def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret", request=request)

    with pytest.raises(ContentIntegrationError):
        await HttpxV2EXTransport(httpx.MockTransport(broken))("feed", "hot", "0", 1)


@pytest.mark.parametrize("status", [401, 500])
async def test_v2ex_http_statuses(status: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(ContentIntegrationError) as exc:
        await HttpxV2EXTransport(httpx.MockTransport(handler))("feed", "hot", "0", 1)
    assert exc.value.code is (
        IntegrationErrorCode.ACCESS_DENIED
        if status == 401
        else IntegrationErrorCode.PROVIDER_UNAVAILABLE
    )
