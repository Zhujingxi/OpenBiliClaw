from __future__ import annotations

import json

import httpx
import pytest

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.providers.weibo import client as weibo_client
from openbiliclaw.content.providers.weibo.client import HttpxWeiboTransport, WeiboClient

_RETURN_URL = (
    "https://m.weibo.cn/api/container/getIndex?containerid=100103type%3D64%26q%3Dopen-source&page=1"
)


def _entry(return_url: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8"},
        text=(
            'var request_id = "request_1";\n'
            f'var return_url = "{return_url}";\n'
            'fetch("/visitor/genvisitor2?cb=visitorCallback&ver=1.0&request_id=request_1");'
        ),
    )


def _generated(sub: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "application/javascript"},
        text=(f'visitorCallback({{"retcode":20000000,"data":{{"sub":"{sub}","alt":""}}}});'),
    )


def _search_mblog() -> dict[str, object]:
    return {
        "id": "5012345678901234",
        "bid": "P0stBid",
        "text": "<p>Open <b>source</b> &amp; tools</p><script>bad()</script>",
        "created_at": "Thu Jan 02 00:00:00 +0800 2025",
        "user": {"screen_name": "alice", "id": 42},
        "extra": "must not leak",
    }


def _search_payload() -> dict[str, object]:
    return {
        "ok": 1,
        "data": {
            "cards": [{"mblog": _search_mblog()}],
            "cardlistInfo": {"page": 2, "total": 10},
        },
    }


async def test_weibo_visitor_reads_sub_from_exchange_jsonp_body() -> None:
    """When the exchange sets no Set-Cookie, the SUB comes from the JSONP body."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "visitor.passport.weibo.cn" and request.method == "GET":
            return _entry(str(request.url.params["url"]))
        if request.url.path.endswith("/genvisitor2"):
            return httpx.Response(
                200,
                text=(
                    'visitorCallback({"retcode":20000000,'
                    '"data":{"tid":"visitor-tid","confidence":"90"}});'
                ),
            )
        if request.url.host == "passport.weibo.com":
            return httpx.Response(
                200,
                text=('visitorCallback({"retcode":20000000,"data":{"sub":"jsonp-sub","alt":""}});'),
            )
        assert request.headers["cookie"] == "SUB=jsonp-sub"
        return httpx.Response(200, json=_search_payload())

    page = json.loads(
        await HttpxWeiboTransport(httpx.MockTransport(handler)).search("open-source", None, 1, None)
    )
    assert page["items"][0]["id"] == "5012345678901234"


async def test_weibo_transport_acquires_in_memory_visitor_and_maps_search() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "visitor.passport.weibo.cn" and request.method == "GET":
            return _entry(str(request.url.params["url"]))
        if request.url.path.endswith("/genvisitor2"):
            assert request.method == "POST"
            return _generated("anonymous-one")
        assert request.url.path == "/api/container/getIndex"
        assert request.url.params["containerid"] == "100103type=64&q=open-source"
        assert request.url.params["page"] == "1"
        assert request.headers["cookie"] == "SUB=anonymous-one"
        assert "authorization" not in request.headers
        return httpx.Response(200, json=_search_payload())

    transport = HttpxWeiboTransport(httpx.MockTransport(handler))
    page = json.loads(await transport.search("open-source", None, 20, None))
    assert page == {
        "items": [
            {
                "id": "5012345678901234",
                "title": "Open source & tools",
                "body": "Open source & tools",
                "author": "alice",
                "url": "https://weibo.com/status/P0stBid",
                "published_at": 1735747200,
                "deleted": False,
            }
        ],
        "next_cursor": "2",
    }
    assert len(calls) == 3
    assert transport.open_client_count == 0


async def test_weibo_transport_retries_soft_blocked_search_with_fresh_visitor() -> None:
    """Upstream randomly returns ok=0 soft-blocks to visitor cookies; retry fresh."""
    generated = iter(("anonymous-one", "anonymous-two"))
    search_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal search_calls
        if request.url.host == "visitor.passport.weibo.cn" and request.method == "GET":
            return _entry(str(request.url.params["url"]))
        if request.url.path.endswith("/genvisitor2"):
            return _generated(next(generated))
        search_calls += 1
        if search_calls == 1:
            return httpx.Response(200, json={"ok": 0, "msg": "这里还没有内容", "data": {}})
        return httpx.Response(200, json=_search_payload())

    page = json.loads(
        await HttpxWeiboTransport(httpx.MockTransport(handler)).search(
            "open-source", None, 20, None
        )
    )
    assert page["items"][0]["id"] == "5012345678901234"
    assert search_calls == 2


async def test_weibo_transport_fails_after_bounded_soft_block_retries() -> None:
    generated = iter(f"anonymous-{index}" for index in range(10))

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "visitor.passport.weibo.cn" and request.method == "GET":
            return _entry(str(request.url.params["url"]))
        if request.url.path.endswith("/genvisitor2"):
            return _generated(next(generated))
        return httpx.Response(200, json={"ok": 0, "msg": "这里还没有内容", "data": {}})

    with pytest.raises(ContentIntegrationError) as exc:
        await HttpxWeiboTransport(httpx.MockTransport(handler)).search(
            "open-source", None, 20, None
        )
    assert exc.value.code is IntegrationErrorCode.PROVIDER_UNAVAILABLE


async def test_weibo_transport_refreshes_rejected_sub_once() -> None:
    generated = iter(("anonymous-one", "anonymous-two"))
    api_cookies: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "visitor.passport.weibo.cn" and request.method == "GET":
            return _entry(str(request.url.params["url"]))
        if request.url.path.endswith("/genvisitor2"):
            return _generated(next(generated))
        api_cookies.append(request.headers["cookie"])
        if len(api_cookies) == 1:
            return httpx.Response(403)
        return httpx.Response(200, json=_search_payload())

    page = json.loads(
        await HttpxWeiboTransport(httpx.MockTransport(handler)).search("open-source", None, 1, None)
    )
    assert page["items"][0]["id"] == "5012345678901234"
    assert api_cookies == ["SUB=anonymous-one", "SUB=anonymous-two"]


async def test_weibo_transport_rejects_second_fresh_sub_failure() -> None:
    generated = iter(f"anonymous-{index}" for index in range(10))

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "visitor.passport.weibo.cn" and request.method == "GET":
            return _entry(str(request.url.params["url"]))
        if request.url.path.endswith("/genvisitor2"):
            return _generated(next(generated))
        return httpx.Response(403)

    with pytest.raises(ContentIntegrationError) as exc:
        await HttpxWeiboTransport(httpx.MockTransport(handler)).search("x", None, 1, None)
    assert exc.value.code is IntegrationErrorCode.ACCESS_DENIED


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (429, IntegrationErrorCode.RATE_LIMITED),
        (500, IntegrationErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
async def test_weibo_transport_classifies_non_refresh_statuses(
    status: int, code: IntegrationErrorCode
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "visitor.passport.weibo.cn" and request.method == "GET":
            return _entry(str(request.url.params["url"]))
        if request.url.path.endswith("/genvisitor2"):
            return _generated("anonymous")
        return httpx.Response(status, content=b"secret response body")

    with pytest.raises(ContentIntegrationError) as exc:
        await HttpxWeiboTransport(httpx.MockTransport(handler)).search("x", None, 1, None)
    assert exc.value.code is code
    assert "secret response body" not in str(exc.value)


async def test_weibo_visitor_uses_passport_exchange_when_generator_returns_tid() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "visitor.passport.weibo.cn" and request.method == "GET":
            return _entry(str(request.url.params["url"]))
        if request.url.path.endswith("/genvisitor2"):
            return httpx.Response(
                200,
                text=(
                    'visitorCallback({"retcode":20000000,'
                    '"data":{"tid":"visitor-tid","confidence":"90"}});'
                ),
            )
        if request.url.host == "passport.weibo.com":
            assert request.url.params["a"] == "incarnate"
            assert request.url.params["t"] == "visitor-tid"
            return httpx.Response(200, headers={"set-cookie": "SUB=exchanged-sub; Path=/"})
        assert request.headers["cookie"] == "SUB=exchanged-sub"
        return httpx.Response(200, json=_search_payload())

    page = json.loads(
        await HttpxWeiboTransport(httpx.MockTransport(handler)).search("open-source", None, 1, None)
    )
    assert page["items"][0]["id"] == "5012345678901234"


def test_weibo_normalizers_cover_upstream_variants() -> None:
    assert weibo_client._timestamp(7) == 7
    assert weibo_client._timestamp(None) == 0
    assert weibo_client._timestamp("2025-01-02T00:00:00+0000") == 1735776000
    assert weibo_client._timestamp("wrong") == 0
    assert weibo_client._js_string(r"https:\/\/m.weibo.cn") == "https://m.weibo.cn"
    with pytest.raises(ValueError, match="invalid visitor string"):
        weibo_client._js_string(r"bad\x")
    assert weibo_client._safe_cookie(" visitor ") == "visitor"
    assert weibo_client._safe_cookie(None) == ""
    assert weibo_client._safe_cookie("bad;cookie") == ""
    assert weibo_client._safe_cookie("bad\x00cookie") == ""
    with pytest.raises(ValueError, match="canonical bid"):
        weibo_client._mblog({"id": "1", "text": "body", "user": {}})
    numeric = weibo_client._mblog(
        {
            "id": 123,
            "bid": "Bid",
            "text": "x" * 120 + "全文",
            "created_at": 9,
            "user": {"name": "fallback"},
        }
    )
    assert numeric.id == "123"
    assert numeric.title.endswith("…")
    assert numeric.author == "fallback"
    nested = weibo_client._mblogs(
        [None, {"card_group": [{"mblog": {"id": "1", "bid": "B", "text": "body"}}]}],
        1,
    )
    assert nested[0].id == "1"
    with pytest.raises(ValueError, match="invalid cards"):
        weibo_client._mblogs("wrong", 1)
    with pytest.raises(ValueError, match="invalid visitor JSONP"):
        weibo_client._jsonp("wrong", "callback")


async def test_weibo_transport_rejects_invalid_envelopes() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="not-an-envelope")

    transport = HttpxWeiboTransport(httpx.MockTransport(handler))
    transport._visitor_sub = "anonymous"  # noqa: SLF001 - isolate envelopes
    with pytest.raises(ContentIntegrationError):
        await transport.search("x", None, 1, None)


async def test_weibo_client_wraps_invalid_typed_payloads() -> None:
    class InvalidTransport:
        async def search(
            self, text: str, cursor: str | None, limit: int, credential: str | None
        ) -> bytes:
            return b"{}"

    client = WeiboClient(InvalidTransport())
    assert "anonymous" in repr(client)
    with pytest.raises(ContentIntegrationError):
        await client.search("x", None, 1, None)


async def test_weibo_transport_wraps_transport_invalid_json_and_invalid_shape() -> None:
    async def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(ContentIntegrationError) as transport_error:
        await HttpxWeiboTransport(httpx.MockTransport(unavailable)).search("x", None, 1, None)
    assert transport_error.value.code is IntegrationErrorCode.PROVIDER_UNAVAILABLE

    async def invalid(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    transport = HttpxWeiboTransport(httpx.MockTransport(invalid))
    transport._visitor_sub = "anonymous"  # noqa: SLF001 - isolate invalid JSON
    with pytest.raises(ContentIntegrationError) as invalid_error:
        await transport.search("x", None, 1, None)
    assert invalid_error.value.code is IntegrationErrorCode.PROVIDER_UNAVAILABLE

    async def wrong_shape(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": 1, "data": {"cards": "wrong"}})

    transport = HttpxWeiboTransport(httpx.MockTransport(wrong_shape))
    transport._visitor_sub = "anonymous"  # noqa: SLF001 - isolate response-shape behavior
    with pytest.raises(ContentIntegrationError) as shape_error:
        await transport.search("x", None, 1, None)
    assert shape_error.value.code is IntegrationErrorCode.PROVIDER_UNAVAILABLE
