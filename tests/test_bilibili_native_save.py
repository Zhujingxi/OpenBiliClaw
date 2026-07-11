from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from openbiliclaw.bilibili.api import (
    BilibiliAPIClient,
    BilibiliAPIError,
    BilibiliAuthExpiredError,
    FavoriteFolder,
    VideoInfo,
)


def _authenticated_client() -> BilibiliAPIClient:
    return BilibiliAPIClient(cookie="SESSDATA=s; bili_jct=csrf; DedeUserID=1")


async def test_add_video_to_watch_later_posts_aid_and_csrf() -> None:
    client = _authenticated_client()
    client.get_video_info = AsyncMock(return_value=VideoInfo(bvid="BV1", aid=42))
    post = AsyncMock(return_value={})
    client._post_json = post

    await client.add_video_to_watch_later("BV1")

    post.assert_awaited_once_with("/x/v2/history/toview/add", data={"aid": 42, "csrf": "csrf"})


async def test_add_video_to_favorite_posts_resource_shape() -> None:
    client = _authenticated_client()
    client.get_video_info = AsyncMock(return_value=VideoInfo(bvid="BV1", aid=42))
    post = AsyncMock(return_value={})
    client._post_json = post

    await client.add_video_to_favorite("BV1", 7)

    post.assert_awaited_once_with(
        "/x/v3/fav/resource/deal",
        data={
            "rid": 42,
            "type": 2,
            "add_media_ids": "7",
            "del_media_ids": "",
            "csrf": "csrf",
        },
    )


async def test_ensure_favorite_folder_reuses_exact_title() -> None:
    client = _authenticated_client()
    existing = FavoriteFolder(media_id=7, title="OpenBiliClaw")
    client.get_favorite_folders = AsyncMock(
        return_value=[FavoriteFolder(media_id=3, title="openbiliclaw"), existing]
    )
    post = AsyncMock(return_value={"id": 99})
    client._post_json = post

    result = await client.ensure_favorite_folder("OpenBiliClaw")

    assert result is existing
    post.assert_not_awaited()


async def test_ensure_favorite_folder_rejects_invalid_existing_id() -> None:
    client = _authenticated_client()
    client.get_favorite_folders = AsyncMock(
        return_value=[FavoriteFolder(media_id=0, title="OpenBiliClaw")]
    )
    post = AsyncMock(return_value={"id": 99})
    client._post_json = post

    with pytest.raises(BilibiliAPIError, match="invalid favorite folder id"):
        await client.ensure_favorite_folder("OpenBiliClaw")

    post.assert_not_awaited()


async def test_ensure_favorite_folder_creates_and_maps_id() -> None:
    client = _authenticated_client()
    client.get_favorite_folders = AsyncMock(return_value=[])
    post = AsyncMock(return_value={"id": 9})
    client._post_json = post

    result = await client.ensure_favorite_folder("OpenBiliClaw")

    post.assert_awaited_once_with(
        "/x/v3/fav/folder/add",
        data={
            "title": "OpenBiliClaw",
            "intro": "",
            "privacy": 0,
            "csrf": "csrf",
        },
    )
    assert result == FavoriteFolder(media_id=9, title="OpenBiliClaw")


@pytest.mark.parametrize("returned_id", [None, "", 0, -1, "not-an-id"])
async def test_ensure_favorite_folder_rejects_invalid_created_id(
    returned_id: object,
) -> None:
    client = _authenticated_client()
    client.get_favorite_folders = AsyncMock(return_value=[])
    client._post_json = AsyncMock(return_value={"id": returned_id})

    with pytest.raises(BilibiliAPIError, match="invalid favorite folder id"):
        await client.ensure_favorite_folder("OpenBiliClaw")


@pytest.mark.parametrize(
    "cookie",
    ["", "bili_jct=csrf", "SESSDATA=s", "SESSDATA=; bili_jct=csrf"],
)
async def test_native_save_rejects_incomplete_auth_before_lookup(cookie: str) -> None:
    client = BilibiliAPIClient(cookie=cookie)
    lookup = AsyncMock(return_value=VideoInfo(bvid="BV1", aid=42))
    post = AsyncMock(return_value={})
    client.get_video_info = lookup
    client._post_json = post

    with pytest.raises(BilibiliAuthExpiredError, match="login required"):
        await client.add_video_to_watch_later("BV1")

    lookup.assert_not_awaited()
    post.assert_not_awaited()


async def test_post_json_rejects_missing_auth_before_network() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=s")
    post = AsyncMock()
    client._client.post = post

    with pytest.raises(BilibiliAuthExpiredError, match="login required"):
        await client._post_json("/write", data={"csrf": "not-used"})

    post.assert_not_awaited()


async def test_post_json_maps_http_failure_without_response_body() -> None:
    request = httpx.Request("POST", "https://api.bilibili.com/write")
    response = httpx.Response(500, request=request, text="private response body")

    def fail(_: httpx.Request) -> httpx.Response:
        return response

    client = _authenticated_client()
    old_http = client._client
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    await old_http.aclose()
    try:
        with pytest.raises(BilibiliAPIError) as captured:
            await client._post_json("/write", data={"csrf": "csrf"})
    finally:
        await client.close()

    assert "private response body" not in str(captured.value)
    assert "csrf" not in str(captured.value)


@pytest.mark.parametrize("code", [-352, -412, -509])
async def test_post_json_preserves_rate_control_code_without_response_body(code: int) -> None:
    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"code": code, "message": "private response body", "data": None},
        )

    client = _authenticated_client()
    old_http = client._client
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(rate_limited))
    await old_http.aclose()
    try:
        with pytest.raises(BilibiliAPIError) as captured:
            await client._post_json("/write", data={"csrf": "csrf"})
    finally:
        await client.close()

    assert captured.value.code == code
    assert str(code) in str(captured.value)
    assert "private response body" not in str(captured.value)


async def test_post_json_maps_minus_101_to_expired_auth() -> None:
    def expired(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"code": -101, "message": "private response body", "data": None},
        )

    client = _authenticated_client()
    old_http = client._client
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(expired))
    await old_http.aclose()
    try:
        with pytest.raises(BilibiliAuthExpiredError) as captured:
            await client._post_json("/write", data={"csrf": "csrf"})
    finally:
        await client.close()

    assert captured.value.code == -101
    assert "private response body" not in str(captured.value)
