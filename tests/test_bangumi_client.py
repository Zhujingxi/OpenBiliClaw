from __future__ import annotations

import json

import httpx
import pytest

from openbiliclaw.sources.bangumi_client import (
    BANGUMI_USER_AGENT,
    BangumiAPIError,
    BangumiClient,
    me_username,
    resolve_access_token_identity,
    subject_type_id,
    validate_bangumi_access_token,
    validate_bangumi_username,
)


@pytest.mark.asyncio
async def test_search_uses_official_endpoint_headers_and_filter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v0/search/subjects"
        assert request.url.params["limit"] == "50"
        assert request.url.params["offset"] == "0"
        assert request.headers["User-Agent"] == BANGUMI_USER_AGENT
        assert request.headers["Accept"] == "application/json"
        body = json.loads(request.content)
        assert body == {
            "keyword": "科幻",
            "sort": "match",
            "filter": {"type": [2, 1], "nsfw": False},
        }
        return httpx.Response(200, json={"data": [{"id": 1}], "total": 1})

    async with httpx.AsyncClient(
        base_url="https://api.bgm.tv", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = BangumiClient(http_client=http_client, request_interval_seconds=0)
        page = await client.search_subjects(
            "科幻", subject_types=("anime", "book"), limit=99, offset=-2
        )

    assert page.total == 1
    assert page.limit == 50
    assert page.offset == 0


@pytest.mark.asyncio
async def test_browse_and_public_collections_use_correct_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [], "total": 0})

    async with httpx.AsyncClient(
        base_url="https://api.bgm.tv", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = BangumiClient(http_client=http_client, request_interval_seconds=0)
        await client.browse_subjects("anime", sort="rank", limit=5, offset=4)
        await client.get_user_collections(
            "测试 用户", collection_type=3, subject_type="game", limit=4, offset=2
        )

    assert requests[0].url.path == "/v0/subjects"
    assert dict(requests[0].url.params) == {
        "type": "2",
        "sort": "rank",
        "limit": "5",
        "offset": "4",
    }
    assert requests[1].url.path.endswith("/collections")
    assert "%E6%B5%8B%E8%AF%95%20%E7%94%A8%E6%88%B7" in str(requests[1].url)
    assert dict(requests[1].url.params) == {
        "limit": "4",
        "offset": "2",
        "type": "3",
        "subject_type": "4",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "invalid_request"), (404, "not_found"), (500, "upstream_error")],
)
async def test_http_errors_are_stable(status: int, code: str) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text="unsafe body"))
    async with httpx.AsyncClient(base_url="https://api.bgm.tv", transport=transport) as http_client:
        client = BangumiClient(http_client=http_client, request_interval_seconds=0)
        with pytest.raises(BangumiAPIError) as exc_info:
            await client.browse_subjects("anime", sort="rank", limit=1)
    assert exc_info.value.code == code
    assert "unsafe body" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_rate_limit_exposes_bounded_retry_after() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(429, headers={"Retry-After": "120"})
    )
    async with httpx.AsyncClient(base_url="https://api.bgm.tv", transport=transport) as http_client:
        client = BangumiClient(http_client=http_client, request_interval_seconds=0)
        with pytest.raises(BangumiAPIError) as exc_info:
            await client.browse_subjects("anime", sort="rank", limit=1)
    assert exc_info.value.code == "rate_limited"
    assert exc_info.value.retry_after_seconds == 120


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "upstream"])
async def test_transient_failure_retries_once_then_succeeds(failure: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [], "total": 0})

    async with httpx.AsyncClient(
        base_url="https://api.bgm.tv", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = BangumiClient(
            http_client=http_client,
            request_interval_seconds=0,
            transient_retry_delay_seconds=0,
        )
        page = await client.browse_subjects("anime", sort="rank", limit=1)

    assert page.data == []
    assert calls == 2


@pytest.mark.asyncio
async def test_empty_page_is_valid_but_schema_drift_is_not() -> None:
    responses = iter(
        [
            httpx.Response(200, json={"data": [], "total": 0}),
            httpx.Response(200, json={"items": [], "total": 0}),
        ]
    )
    transport = httpx.MockTransport(lambda request: next(responses))
    async with httpx.AsyncClient(base_url="https://api.bgm.tv", transport=transport) as http_client:
        client = BangumiClient(http_client=http_client, request_interval_seconds=0)
        page = await client.browse_subjects("anime", sort="rank", limit=1)
        assert page.data == []
        with pytest.raises(BangumiAPIError, match="shape") as exc_info:
            await client.browse_subjects("anime", sort="rank", limit=1)
    assert exc_info.value.code == "schema_changed"


def test_subject_type_and_username_validation() -> None:
    assert subject_type_id("anime") == 2
    assert validate_bangumi_username("  sai  ") == "sai"
    with pytest.raises(ValueError):
        subject_type_id("movie")
    with pytest.raises(ValueError):
        validate_bangumi_username("bad/name")


def test_access_token_validation() -> None:
    assert validate_bangumi_access_token("  tok-123  ") == "tok-123"
    assert validate_bangumi_access_token(None) == ""
    assert validate_bangumi_access_token("") == ""
    with pytest.raises(ValueError):
        validate_bangumi_access_token("has space\nnewline")
    with pytest.raises(ValueError):
        validate_bangumi_access_token("x" * 513)


def test_me_username_defensive_parsing() -> None:
    assert me_username({"username": "  sai  ", "nickname": "Sai"}) == "sai"
    with pytest.raises(BangumiAPIError) as exc_info:
        me_username({"nickname": "Sai"})
    assert exc_info.value.code == "schema_changed"


@pytest.mark.asyncio
async def test_token_client_sends_bearer_on_every_request() -> None:
    seen_auth: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("Authorization"))
        if request.url.path == "/v0/me":
            return httpx.Response(200, json={"username": "sai", "nickname": "Sai"})
        return httpx.Response(200, json={"data": [], "total": 0})

    async with httpx.AsyncClient(
        base_url="https://api.bgm.tv", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = BangumiClient(
            http_client=http_client, access_token="  tok-123  ", request_interval_seconds=0
        )
        assert client.has_access_token is True
        assert me_username(await client.get_me()) == "sai"
        await client.get_user_collections("sai", limit=5)

    assert seen_auth == ["Bearer tok-123", "Bearer tok-123"]


@pytest.mark.asyncio
async def test_anonymous_client_sends_no_bearer_and_get_me_requires_token() -> None:
    seen_auth: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"data": [], "total": 0})

    async with httpx.AsyncClient(
        base_url="https://api.bgm.tv", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = BangumiClient(http_client=http_client, request_interval_seconds=0)
        assert client.has_access_token is False
        await client.browse_subjects("anime", sort="rank", limit=1)
        with pytest.raises(BangumiAPIError) as exc_info:
            await client.get_me()

    assert seen_auth == [None]
    assert exc_info.value.code == "unauthorized"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_get_me_maps_unauthorized(status: int) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text="denied"))
    async with httpx.AsyncClient(base_url="https://api.bgm.tv", transport=transport) as http_client:
        client = BangumiClient(
            http_client=http_client, access_token="tok", request_interval_seconds=0
        )
        with pytest.raises(BangumiAPIError) as exc_info:
            await client.get_me()
    assert exc_info.value.code == "unauthorized"
    assert "denied" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_disable_access_token_degrades_to_anonymous() -> None:
    seen_auth: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"data": [], "total": 0})

    async with httpx.AsyncClient(
        base_url="https://api.bgm.tv", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = BangumiClient(
            http_client=http_client, access_token="tok", request_interval_seconds=0
        )
        await client.browse_subjects("anime", sort="rank", limit=1)
        client.disable_access_token()
        assert client.has_access_token is False
        await client.browse_subjects("anime", sort="rank", limit=1)

    assert seen_auth == ["Bearer tok", None]


@pytest.mark.asyncio
async def test_resolve_access_token_identity_returns_username_or_raises() -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v0/me"
        return httpx.Response(200, json={"username": "sai"})

    async with httpx.AsyncClient(
        base_url="https://api.bgm.tv", transport=httpx.MockTransport(ok_handler)
    ) as http_client:
        client = BangumiClient(
            http_client=http_client, access_token="tok", request_interval_seconds=0
        )
        assert me_username(await client.get_me()) == "sai"

    with pytest.raises(ValueError):
        await resolve_access_token_identity("")
