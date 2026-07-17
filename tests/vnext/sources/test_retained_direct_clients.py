"""Focused contracts for direct source clients retained by the vNext composition."""

from __future__ import annotations

import ast
from pathlib import Path
from types import MethodType
from typing import Any

import httpx
import pytest
from twitter_cli.client import TwitterAPIError
from twitter_cli.exceptions import AuthenticationError
from twitter_cli.models import Author, Metrics, Tweet

from openbiliclaw.infrastructure.jobs.tasks import classify_retry
from openbiliclaw.infrastructure.sources import youtube_client
from openbiliclaw.infrastructure.sources.bilibili_client import (
    BilibiliAPIClient,
    BilibiliAPIError,
    BilibiliAuthExpiredError,
)
from openbiliclaw.infrastructure.sources.douyin_client import (
    DouyinDirectAuthError,
    DouyinDirectClient,
    DouyinDirectError,
    DouyinDirectRateLimitError,
    DouyinDirectSignatureError,
    DouyinDirectTransportError,
    parse_cookie_header,
)
from openbiliclaw.infrastructure.sources.twitter_client import (
    XAuthError,
    XBlockedError,
    XClient,
    XClientError,
    XMissingCookieError,
    XRateLimitError,
)
from openbiliclaw.infrastructure.sources.youtube_client import (
    YtScraperClient,
    _channel_uploads_url,
)

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "src" / "openbiliclaw"


def test_retained_direct_client_types_are_infrastructure_owned() -> None:
    assert BilibiliAPIClient.__module__ == "openbiliclaw.infrastructure.sources.bilibili_client"
    assert BilibiliAPIError.__module__ == "openbiliclaw.infrastructure.sources.bilibili_client"
    assert DouyinDirectClient.__module__ == "openbiliclaw.infrastructure.sources.douyin_client"
    assert XClient.__module__ == "openbiliclaw.infrastructure.sources.twitter_client"
    assert YtScraperClient.__module__ == "openbiliclaw.infrastructure.sources.youtube_client"


def test_vnext_source_composition_has_no_legacy_source_graph_imports() -> None:
    forbidden = (
        "openbiliclaw.discovery",
        "openbiliclaw.saved_sync",
        "openbiliclaw.sources",
        "openbiliclaw.storage",
        "openbiliclaw.youtube",
    )
    paths = (
        PACKAGE / "infrastructure" / "jobs" / "source_composition.py",
        *(PACKAGE / "infrastructure" / "sources").glob("*.py"),
    )
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported = ""
            if isinstance(node, ast.ImportFrom):
                imported = node.module or ""
            elif isinstance(node, ast.Import):
                imported = node.names[0].name
            if imported.startswith(forbidden):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {imported}")
    assert violations == []


def test_reddit_has_no_orphaned_cli_backend_or_dependency() -> None:
    assert not (PACKAGE / "infrastructure" / "sources" / "reddit_cli.py").exists()
    assert "rdt-cli" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_douyin_direct_client_rejects_empty_credentials_without_network() -> None:
    assert parse_cookie_header(" msToken = abc ; ttwid=tw ; invalid ; empty= ") == {
        "msToken": "abc",
        "ttwid": "tw",
    }
    with pytest.raises(DouyinDirectAuthError):
        DouyinDirectClient(cookie="")


def test_twitter_client_rejects_incomplete_cookie_before_network() -> None:
    with pytest.raises(XMissingCookieError):
        XClient(cookie="auth_token=only-token")._auth_pair()


def test_youtube_client_keeps_supported_creator_reference_shapes() -> None:
    assert _channel_uploads_url("@creator") == "https://www.youtube.com/@creator/videos"
    assert _channel_uploads_url("UC123") == "https://www.youtube.com/channel/UC123/videos"
    assert _channel_uploads_url("") == ""


def _bilibili_client(handler: Any, *, cookie: str = "SESSDATA=test") -> BilibiliAPIClient:
    return BilibiliAPIClient(
        cookie=cookie,
        min_request_interval=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_bilibili_production_client_exercises_every_retained_direct_capability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/x/web-interface/nav":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "isLogin": True,
                        "mid": 7,
                        "wbi_img": {
                            "img_url": "https://i0.hdslb.com/bfs/wbi/" + "a" * 64 + ".png",
                            "sub_url": "https://i0.hdslb.com/bfs/wbi/" + "b" * 64 + ".png",
                        },
                    },
                },
            )
        payloads: dict[str, object] = {
            "/x/web-interface/wbi/search/type": {"result": [{"bvid": "BV-search"}]},
            "/x/web-interface/history/cursor": {
                "list": [{"bvid": "BV-history"}],
                "cursor": {},
            },
            "/x/v3/fav/folder/created/list-all": {
                "list": [{"id": 11, "title": "Saved", "media_count": 1}]
            },
            "/x/v3/fav/resource/list": {
                "medias": [{"bvid": "BV-favorite"}],
                "has_more": False,
            },
            "/x/relation/followings": {"list": [{"mid": 9, "uname": "Creator", "sign": "bio"}]},
            "/x/web-interface/ranking/v2": {"list": [{"bvid": "BV-hot"}]},
            "/x/web-interface/archive/related": [{"bvid": "BV-related"}],
        }
        return httpx.Response(200, json={"code": 0, "data": payloads[path]})

    client = _bilibili_client(handler)
    try:
        assert [row["bvid"] for row in await client.search("typed", page_size=1)] == ["BV-search"]
        assert [row["bvid"] for row in await client.get_user_history(1)] == ["BV-history"]
        folders = await client.get_all_favorites(max_total_items=1)
        assert folders[0].items[0]["bvid"] == "BV-favorite"
        assert (await client.get_following(page_size=1))[0].mid == 9
        assert (await client.get_ranking())[0]["bvid"] == "BV-hot"
        assert (await client.get_related_videos("BV-seed"))[0]["bvid"] == "BV-related"
    finally:
        await client.close()


async def test_bilibili_client_distinguishes_empty_malformed_auth_timeout_and_rate_limit() -> None:
    empty = _bilibili_client(
        lambda request: httpx.Response(
            200,
            json={
                "code": 0,
                "data": [] if request.url.path.endswith("/related") else {},
            },
        )
    )
    try:
        assert await empty.get_ranking() == []
        assert await empty.get_related_videos("BV-empty") == []
    finally:
        await empty.close()

    malformed = _bilibili_client(lambda _request: httpx.Response(200, text="not-json"))
    try:
        with pytest.raises(BilibiliAPIError, match="malformed") as malformed_error:
            await malformed.get_ranking()
        assert classify_retry(malformed_error.value) is False
        with pytest.raises(BilibiliAPIError, match="malformed"):
            await malformed.search("typed", page_size=1)
    finally:
        await malformed.close()

    malformed_code = _bilibili_client(
        lambda _request: httpx.Response(200, json={"code": "private-invalid-code", "data": {}})
    )
    try:
        with pytest.raises(BilibiliAPIError, match="malformed code") as code_error:
            await malformed_code.get_ranking()
        assert "private-invalid-code" not in str(code_error.value)
        assert classify_retry(code_error.value) is False
    finally:
        await malformed_code.close()

    expired = _bilibili_client(
        lambda _request: httpx.Response(200, json={"code": -101, "data": {}})
    )
    try:
        with pytest.raises(BilibiliAuthExpiredError) as auth_error:
            await expired.get_user_history(1)
        assert classify_retry(auth_error.value) is False
    finally:
        await expired.close()

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret upstream detail", request=request)

    timed_out = _bilibili_client(timeout_handler)
    try:
        with pytest.raises(BilibiliAPIError, match="failed") as timeout_error:
            await timed_out.get_related_videos("BV-timeout")
        assert "secret upstream detail" not in str(timeout_error.value)
        assert classify_retry(timeout_error.value) is True
    finally:
        await timed_out.close()

    limited = _bilibili_client(lambda _request: httpx.Response(429, json={}))
    try:
        with pytest.raises(BilibiliAPIError) as rate_error:
            await limited.get_ranking()
        assert rate_error.value.code == -429
        assert classify_retry(rate_error.value) is True
    finally:
        await limited.close()

    http_blocked = _bilibili_client(lambda _request: httpx.Response(412, json={}))
    try:
        with pytest.raises(BilibiliAPIError) as blocked_error:
            await http_blocked.get_ranking()
        assert blocked_error.value.code == -412
        assert classify_retry(blocked_error.value) is True
    finally:
        await http_blocked.close()

    application_limited = _bilibili_client(
        lambda _request: httpx.Response(
            200,
            json={"code": -429, "message": "private application detail", "data": {}},
        )
    )
    try:
        with pytest.raises(BilibiliAPIError) as application_rate_error:
            await application_limited.get_ranking()
        assert application_rate_error.value.code == -429
        assert "private application detail" not in str(application_rate_error.value)
        assert classify_retry(application_rate_error.value) is True
    finally:
        await application_limited.close()

    application_blocked = _bilibili_client(
        lambda _request: httpx.Response(
            200,
            json={"code": -412, "message": "private risk-control detail", "data": {}},
        )
    )
    try:
        with pytest.raises(BilibiliAPIError) as application_blocked_error:
            await application_blocked.get_ranking()
        assert application_blocked_error.value.code == -412
        assert "private risk-control detail" not in str(application_blocked_error.value)
        assert classify_retry(application_blocked_error.value) is True
    finally:
        await application_blocked.close()


class _Signer:
    user_agent = "test-agent"

    def sign(self, url: str) -> str:
        return url


def _douyin_client(handler: Any) -> tuple[DouyinDirectClient, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        DouyinDirectClient(
            cookie="msToken=test; ttwid=test",
            http_client=http_client,
            signer=_Signer(),
        ),
        http_client,
    )


async def test_douyin_production_client_exercises_every_retained_direct_capability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("general/search/single/"):
            return httpx.Response(
                200,
                json={"data": [{"aweme_info": {"aweme_id": "search"}}]},
            )
        if request.url.path.endswith("hot/search/list/"):
            return httpx.Response(
                200,
                json={"data": {"word_list": [{"aweme_info": {"aweme_id": "hot"}}]}},
            )
        return httpx.Response(200, json={"aweme_list": [{"aweme_id": "feed"}]})

    client, http_client = _douyin_client(handler)
    try:
        assert (await client.search_aweme("typed", limit=1))[0]["aweme_id"] == "search"
        assert (await client.get_hot_board(limit=1))[0]["aweme_id"] == "hot"
        assert (await client.get_recommend_feed(limit=1))[0]["aweme_id"] == "feed"
    finally:
        await http_client.aclose()


@pytest.mark.parametrize(
    ("handler", "error_type", "retryable"),
    [
        (lambda _request: httpx.Response(200, json={}), None, None),
        (lambda _request: httpx.Response(200, text="not-json"), DouyinDirectError, False),
        (lambda _request: httpx.Response(401, json={}), DouyinDirectAuthError, False),
        (lambda _request: httpx.Response(429, json={}), DouyinDirectRateLimitError, True),
        (lambda _request: httpx.Response(503, json={}), DouyinDirectTransportError, True),
        (
            lambda _request: httpx.Response(200, json={"status_code": 401}),
            DouyinDirectAuthError,
            False,
        ),
        (
            lambda _request: httpx.Response(200, json={"status_code": 429}),
            DouyinDirectRateLimitError,
            True,
        ),
        (
            lambda _request: httpx.Response(200, json={"status_code": 503}),
            DouyinDirectTransportError,
            True,
        ),
        (
            lambda _request: httpx.Response(200, json={"status_code": 2190008}),
            DouyinDirectError,
            False,
        ),
        (
            lambda _request: httpx.Response(200, json={"status_code": "private-invalid-code"}),
            DouyinDirectError,
            False,
        ),
    ],
)
async def test_douyin_client_classifies_empty_malformed_auth_rate_and_http_failures(
    handler: Any,
    error_type: type[Exception] | None,
    retryable: bool | None,
) -> None:
    client, http_client = _douyin_client(handler)
    try:
        if error_type is None:
            assert await client.search_aweme("typed", limit=1) == []
        else:
            with pytest.raises(error_type) as error:
                await client.search_aweme("typed", limit=1)
            assert classify_retry(error.value) is retryable
    finally:
        await http_client.aclose()


async def test_douyin_client_classifies_timeout_and_signing_failure() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private detail", request=request)

    client, http_client = _douyin_client(timeout_handler)
    try:
        with pytest.raises(DouyinDirectTransportError) as timeout_error:
            await client.search_aweme("typed", limit=1)
        assert "private detail" not in str(timeout_error.value)
        assert classify_retry(timeout_error.value) is True
    finally:
        await http_client.aclose()

    class BrokenSigner(_Signer):
        def sign(self, url: str) -> str:
            del url
            raise ValueError("private signer state")

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as http_client:
        signed = DouyinDirectClient(
            cookie="msToken=test",
            http_client=http_client,
            signer=BrokenSigner(),
        )
        with pytest.raises(DouyinDirectSignatureError) as signature_error:
            await signed.search_aweme("typed", limit=1)
        assert "private signer state" not in str(signature_error.value)
        assert classify_retry(signature_error.value) is False


async def test_youtube_production_client_exercises_direct_capabilities_and_filters_malformed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        youtube_client,
        "_scrapetube_search",
        lambda _query, _limit: [{"videoId": "search"}, "malformed"],
    )
    monkeypatch.setattr(
        youtube_client,
        "_innertube_trending",
        lambda _region, _limit: [{"videoId": "hot"}, None],
    )
    monkeypatch.setattr(
        youtube_client,
        "_scrapetube_channel",
        lambda _channel, _limit: [{"videoId": "creator"}, 7],
    )
    client = YtScraperClient()

    assert await client.search_videos("typed", limit=3) == [{"videoId": "search"}]
    assert await client.get_trending(limit=3) == [{"videoId": "hot"}]
    assert await client.get_channel_videos("UC1", limit=3) == [{"videoId": "creator"}]


async def test_youtube_production_client_has_deterministic_empty_timeout_and_rate_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(youtube_client, "_ytdlp_search", lambda _query, _limit: [])
    monkeypatch.setattr(youtube_client, "_ytdlp_channel", lambda _channel, _limit: [])

    class BrokenScrapeTube:
        @staticmethod
        def get_search(*_args: object, **_kwargs: object) -> list[object]:
            raise TimeoutError("timed out")

        @staticmethod
        def get_channel(*_args: object, **_kwargs: object) -> list[object]:
            raise RuntimeError("429 rate limited")

    monkeypatch.setitem(__import__("sys").modules, "scrapetube", BrokenScrapeTube())
    monkeypatch.setattr(youtube_client, "_innertube_trending_feed", lambda _region, _limit: [])
    monkeypatch.setattr(youtube_client, "_topic_page_trending", lambda _region, _limit: [])
    client = YtScraperClient()

    assert await client.search_videos("typed", limit=2) == []
    assert await client.get_trending(limit=2) == []
    assert await client.get_channel_videos("UC1", limit=2) == []


def _tweet(identifier: str = "tweet") -> Tweet:
    return Tweet(
        id=identifier,
        text=identifier,
        author=Author(id="author", name="Author", screen_name="author"),
        metrics=Metrics(),
        created_at="2026-01-01T00:00:00Z",
    )


async def test_x_production_client_exercises_every_retained_direct_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = XClient(cookie="auth_token=test; ct0=test")
    monkeypatch.setattr(client, "_raw_search", lambda *_args, **_kwargs: [_tweet("search")])
    monkeypatch.setattr(client, "_raw_for_you", lambda **_kwargs: [_tweet("feed")])
    monkeypatch.setattr(client, "_raw_user_tweets", lambda *_args, **_kwargs: [_tweet("creator")])
    monkeypatch.setattr(client, "_raw_likes", lambda **_kwargs: [_tweet("liked")])
    monkeypatch.setattr(client, "_raw_bookmarks", lambda **_kwargs: [_tweet("saved")])

    assert (await client.search("typed", limit=1))[0]["id"] == "search"
    assert (await client.for_you(limit=1))[0]["id"] == "feed"
    assert (await client.user_tweets("author", limit=1))[0]["id"] == "creator"
    assert (await client.likes(limit=1))[0]["id"] == "liked"
    assert (await client.bookmarks(limit=1))[0]["id"] == "saved"


@pytest.mark.parametrize(
    ("upstream", "expected", "retryable"),
    [
        (TwitterAPIError(401, "private auth detail"), XAuthError, False),
        (AuthenticationError("private auth detail"), XAuthError, False),
        (TwitterAPIError(403, "private blocked detail"), XBlockedError, False),
        (TwitterAPIError(429, "private rate detail"), XRateLimitError, True),
        (TimeoutError("private timeout detail"), XClientError, True),
    ],
)
async def test_x_client_classifies_auth_timeout_and_rate_errors_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch,
    upstream: Exception,
    expected: type[XClientError],
    retryable: bool,
) -> None:
    client = XClient(cookie="auth_token=test; ct0=test")

    def fail(*_args: object, **_kwargs: object) -> list[Tweet]:
        raise upstream

    monkeypatch.setattr(client, "_raw_search", fail)
    with pytest.raises(expected) as error:
        await client.search("typed", limit=1)
    assert "private" not in str(error.value)
    assert classify_retry(error.value) is retryable


async def test_x_client_returns_empty_and_maps_malformed_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = XClient(cookie="auth_token=test; ct0=test")
    monkeypatch.setattr(client, "_raw_search", lambda *_args, **_kwargs: [])
    assert await client.search("typed", limit=1) == []

    async def malformed_run(self: XClient, *_args: object, **_kwargs: object) -> list[Any]:
        del self
        return [object()]

    monkeypatch.setattr(client, "_run", MethodType(malformed_run, client))
    with pytest.raises(XClientError, match="malformed"):
        await client.search("typed", limit=1)
