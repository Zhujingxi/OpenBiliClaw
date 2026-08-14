from __future__ import annotations

import json

import httpx
import pytest

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.providers.bangumi.client import HttpxBangumiTransport
from openbiliclaw.content.providers.v2ex.client import HttpxV2EXTransport


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
    with pytest.raises(ValueError, match="unsupported operation"):
        await transport("search", "topic", "0", 5)


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
