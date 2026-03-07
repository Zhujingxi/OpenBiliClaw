"""Tests for Bilibili API helpers."""

from __future__ import annotations

import pytest

from openbiliclaw.bilibili.api import (
    BilibiliAPIClient,
    BilibiliAPIError,
    CommentInfo,
    FavoriteFolder,
    FavoriteFolderWithItems,
    FollowingUser,
)


class FakeResponse:
    """Minimal fake HTTP response."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class FakeAsyncClient:
    """Minimal fake async HTTP client."""

    def __init__(self, payload: dict[str, object] | list[dict[str, object]]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def get(self, url: str, params: dict[str, object] | None = None) -> FakeResponse:
        self.calls.append((url, params))
        payload = self.payload.pop(0) if isinstance(self.payload, list) else self.payload
        return FakeResponse(payload)

    async def aclose(self) -> None:
        return None


class RouteAsyncClient:
    """Route-aware fake async HTTP client."""

    def __init__(self, routes: dict[str, list[dict[str, object]]]) -> None:
        self.routes = {key: value.copy() for key, value in routes.items()}
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def get(self, url: str, params: dict[str, object] | None = None) -> FakeResponse:
        self.calls.append((url, params))
        for path, payloads in self.routes.items():
            if url.endswith(path):
                return FakeResponse(payloads.pop(0))
        raise AssertionError(f"Unexpected URL: {url}")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_get_nav_info_parses_login_payload() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    fake_http = FakeAsyncClient(
        {
            "code": 0,
            "data": {
                "isLogin": True,
                "uname": "alice",
                "mid": 10086,
            },
        }
    )
    client._client = fake_http

    nav = await client.get_nav_info()

    assert nav.is_login is True
    assert nav.uname == "alice"
    assert nav.mid == 10086
    assert fake_http.calls[0][0].endswith("/x/web-interface/nav")


@pytest.mark.asyncio
async def test_get_nav_info_raises_on_nonzero_code() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = FakeAsyncClient({"code": -101, "message": "账号未登录"})

    with pytest.raises(BilibiliAPIError, match="账号未登录"):
        await client.get_nav_info()


@pytest.mark.asyncio
async def test_get_user_history_uses_cursor_pagination() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = FakeAsyncClient(
        [
            {
                "code": 0,
                "data": {
                    "list": [{"title": "v1"}, {"title": "v2"}],
                    "cursor": {"max": 111, "view_at": 222},
                },
            },
            {
                "code": 0,
                "data": {
                    "list": [{"title": "v3"}, {"title": "v4"}],
                    "cursor": {"max": 0, "view_at": 0},
                },
            },
        ]
    )

    history = await client.get_user_history(max_items=3)

    assert len(history) == 3
    assert client._client.calls[0][1] == {"type": "archive"}
    assert client._client.calls[1][1] == {
        "type": "archive",
        "max": 111,
        "view_at": 222,
    }


@pytest.mark.asyncio
async def test_search_passes_order_parameter() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = FakeAsyncClient({"code": 0, "data": {"result": []}})

    await client.search("纪录片", page=2, page_size=10, order="pubdate")

    assert client._client.calls[0][1] == {
        "keyword": "纪录片",
        "search_type": "video",
        "page": 2,
        "page_size": 10,
        "order": "pubdate",
    }


@pytest.mark.asyncio
async def test_history_request_awaits_rate_limit_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = FakeAsyncClient(
        {"code": 0, "data": {"list": [], "cursor": {"max": 0, "view_at": 0}}}
    )
    calls: list[str] = []

    async def fake_rate_limit() -> None:
        calls.append("rate-limit")

    monkeypatch.setattr(client, "_respect_rate_limit", fake_rate_limit)

    await client.get_user_history(max_items=1)

    assert calls == ["rate-limit"]


@pytest.mark.asyncio
async def test_get_favorite_folders_parses_folder_metadata() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = RouteAsyncClient(
        {
            "/x/web-interface/nav": [
                {"code": 0, "data": {"isLogin": True, "uname": "alice", "mid": 42}}
            ],
            "/x/v3/fav/folder/created/list-all": [
                {
                    "code": 0,
                    "data": {
                        "list": [
                            {"id": 1, "title": "纪录片", "media_count": 12},
                            {"id": 2, "title": "技术", "media_count": 6},
                        ]
                    },
                }
            ]
        }
    )

    folders = await client.get_favorite_folders()

    assert folders == [
        FavoriteFolder(media_id=1, title="纪录片", media_count=12),
        FavoriteFolder(media_id=2, title="技术", media_count=6),
    ]


@pytest.mark.asyncio
async def test_get_all_favorites_respects_budget_limits() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = RouteAsyncClient(
        {
            "/x/web-interface/nav": [
                {"code": 0, "data": {"isLogin": True, "uname": "alice", "mid": 42}}
            ],
            "/x/v3/fav/folder/created/list-all": [
                {
                    "code": 0,
                    "data": {
                        "list": [
                            {"id": 1, "title": "纪录片", "media_count": 12},
                            {"id": 2, "title": "技术", "media_count": 6},
                        ]
                    },
                }
            ],
            "/x/v3/fav/resource/list": [
                {
                    "code": 0,
                    "data": {
                        "medias": [
                            {"title": "v1"},
                            {"title": "v2"},
                            {"title": "v3"},
                        ]
                    },
                }
            ],
        }
    )

    favorites = await client.get_all_favorites(max_folders=1, max_items_per_folder=2)

    assert favorites == [
        FavoriteFolderWithItems(
            folder=FavoriteFolder(media_id=1, title="纪录片", media_count=12),
            items=[{"title": "v1"}, {"title": "v2"}],
            truncated=True,
        )
    ]


@pytest.mark.asyncio
async def test_get_following_parses_users() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = RouteAsyncClient(
        {
            "/x/web-interface/nav": [
                {"code": 0, "data": {"isLogin": True, "uname": "alice", "mid": 42}}
            ],
            "/x/relation/followings": [
                {
                    "code": 0,
                    "data": {
                        "list": [
                            {"mid": 1, "uname": "alice", "sign": "doc lover"},
                            {"mid": 2, "uname": "bob", "sign": "tech"},
                        ]
                    },
                }
            ]
        }
    )

    users = await client.get_following(page=1, page_size=2)

    assert users == [
        FollowingUser(mid=1, uname="alice", sign="doc lover"),
        FollowingUser(mid=2, uname="bob", sign="tech"),
    ]


@pytest.mark.asyncio
async def test_get_video_comments_returns_top_n_comments() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=abc")
    client._client = RouteAsyncClient(
        {
            "/x/web-interface/view": [
                {"code": 0, "data": {"aid": 123, "stat": {}, "owner": {}}}
            ],
            "/x/v2/reply/main": [
                {
                    "code": 0,
                    "data": {
                        "replies": [
                            {
                                "mid": 1,
                                "member": {"uname": "alice"},
                                "content": {"message": "第一条"},
                                "like": 11,
                            },
                            {
                                "mid": 2,
                                "member": {"uname": "bob"},
                                "content": {"message": "第二条"},
                                "like": 7,
                            },
                        ]
                    },
                }
            ]
        }
    )

    comments = await client.get_video_comments("BV1xx", limit=1)

    assert comments == [
        CommentInfo(mid=1, uname="alice", message="第一条", like_count=11),
    ]
