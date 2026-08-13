from __future__ import annotations

import json

import httpx
import pytest

from openbiliclaw.access.models import CredentialAccessHandle, Permission
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.providers.bilibili.client import (
    BilibiliClient,
    HttpxBilibiliTransport,
    cookie_parts,
)
from openbiliclaw.content.providers.bilibili.models import BilibiliVideo


def _handle() -> CredentialAccessHandle:
    return CredentialAccessHandle(
        provider_id="bilibili",
        account_id="42",
        permissions=frozenset({Permission.READ_PRIVATE}),
        credential_ref="cred_" + "a" * 32,
        revision=1,
    )


class _ResponseTransport:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.queries: list[str] = []

    async def __call__(
        self, method: str, path: str, query: str, cookie: str | None, body: bytes
    ) -> bytes:
        self.queries.append(query)
        return self.body


class _Resolver:
    async def __call__(self, handle: CredentialAccessHandle) -> str:
        del handle
        return "fake-cookie"


def test_cookie_parser_requires_exact_names() -> None:
    assert cookie_parts("xSESSDATA=no; SESSDATA=yes; bili_jct=csrf") == ("yes", "csrf")
    assert cookie_parts("SESSDATA=yes") == ("yes", None)


@pytest.mark.asyncio
async def test_real_native_search_fields_are_normalized() -> None:
    transport = _ResponseTransport(
        json.dumps(
            {
                "code": 0,
                "message": "0",
                "data": {
                    "result": [
                        {
                            "id": "12345",
                            "bvid": "BV1TEST12345",
                            "title": '<em class="keyword">Typed</em> video',
                            "description": "real shape",
                            "author": "creator",
                            "mid": "42",
                            "pic": "//i0.hdslb.com/fake.jpg",
                            "pubdate": "1700000000",
                            "duration": "02:03",
                            "play": "1234",
                            "like": "56",
                            "favorites": "7",
                        }
                    ]
                },
            }
        ).encode()
    )
    page = await BilibiliClient(transport, _Resolver()).page(
        "/x/web-interface/search/type", {"keyword": "typed", "limit": 1}
    )
    item = page.items[0]
    assert isinstance(item, BilibiliVideo)
    assert item.title == "Typed video"
    assert item.aid == 12345
    assert item.duration_seconds == 123
    assert item.cover_url == "https://i0.hdslb.com/fake.jpg"
    assert item.stats.views == 1234


@pytest.mark.asyncio
async def test_authenticated_history_and_related_real_shapes() -> None:
    history = _ResponseTransport(
        json.dumps(
            {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "title": "History video",
                            "author_name": "creator",
                            "author_mid": 42,
                            "cover": "//i0.hdslb.com/history.jpg",
                            "duration": 60,
                            "view_at": 1700000000,
                            "history": {"oid": 12345, "bvid": "BV1HIST12345"},
                        }
                    ]
                },
            }
        ).encode()
    )
    page = await BilibiliClient(history, _Resolver()).page(
        "/x/web-interface/history/cursor", {"limit": 3, "cursor": "0"}, _handle()
    )
    assert page.items[0].title == "History video"
    assert "ps=3" in history.queries[0] and "view_at=0" in history.queries[0]

    related = _ResponseTransport(
        json.dumps(
            {
                "code": 0,
                "data": [
                    {
                        "bvid": "BV1REL123456",
                        "aid": 9,
                        "title": "Related video",
                        "duration": 10,
                    }
                ],
            }
        ).encode()
    )
    page = await BilibiliClient(related, _Resolver()).page(
        "/x/web-interface/archive/related", {"bvid": "BV1REL123456", "limit": 1}
    )
    assert page.items[0].title == "Related video"


@pytest.mark.asyncio
async def test_http_transport_validates_response_at_boundary_and_closes_client() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"code": 0, "message": "0", "data": {"items": [], "next_cursor": None}}
        )

    transport = HttpxBilibiliTransport(httpx.MockTransport(handler))
    body = await transport("GET", "/x/web-interface/popular", "limit=1", None, b"")
    assert json.loads(body)["code"] == 0
    assert seen[0].headers["referer"] == "https://www.bilibili.com"
    assert transport.open_client_count == 0


@pytest.mark.asyncio
async def test_http_status_is_safe_and_classified() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="SECRET RAW BODY", request=request)

    transport = HttpxBilibiliTransport(httpx.MockTransport(handler))
    with pytest.raises(ContentIntegrationError) as raised:
        await transport("GET", "/x/test", "", None, b"")
    assert raised.value.code is IntegrationErrorCode.RATE_LIMITED
    assert "SECRET RAW BODY" not in str(raised.value)
