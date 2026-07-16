from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from openbiliclaw.bilibili.api import (
    BilibiliAPIClient,
    BilibiliAPIError,
    BilibiliAuthExpiredError,
    BilibiliFavoriteDuplicateError,
    FavoriteFolder,
)


def _authenticated_client() -> BilibiliAPIClient:
    return BilibiliAPIClient(cookie="SESSDATA=s; bili_jct=csrf; DedeUserID=1")


async def test_add_video_to_watch_later_posts_aid_and_csrf() -> None:
    client = _authenticated_client()
    resolve = AsyncMock(return_value=42)
    client._resolve_aid = resolve
    post = AsyncMock(return_value={})
    client._post_json = post

    await client.add_video_to_watch_later("BV1")

    post.assert_awaited_once_with("/x/v2/history/toview/add", data={"aid": 42, "csrf": "csrf"})
    resolve.assert_awaited_once_with("BV1")


async def test_add_video_to_favorite_posts_resource_shape() -> None:
    client = _authenticated_client()
    resolve = AsyncMock(return_value=42)
    client._resolve_aid = resolve
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
    resolve.assert_awaited_once_with("BV1")


async def test_resource_deal_duplicate_is_operation_tagged_and_sanitized() -> None:
    client = _authenticated_client()
    client._resolve_aid = AsyncMock(return_value=42)
    client._post_json = AsyncMock(
        side_effect=BilibiliAPIError(
            "Cookie=secret; private resource response",
            code=11201,
        )
    )

    with pytest.raises(BilibiliAPIError) as captured:
        await client.add_video_to_favorite("BV1", 7)

    assert isinstance(captured.value, BilibiliFavoriteDuplicateError)
    assert captured.value.code == 11201
    assert str(captured.value) == "Bilibili favorite already contains this video (code 11201)"
    assert isinstance(captured.value.__cause__, BilibiliAPIError)
    assert "secret" not in str(captured.value)
    assert "private resource response" not in str(captured.value)


async def test_resolve_aid_uses_application_aware_view_endpoint() -> None:
    client = _authenticated_client()
    get = AsyncMock(return_value={"aid": 42})
    client._get_json = get

    aid = await client._resolve_aid("BV1")

    assert aid == 42
    get.assert_awaited_once_with("/x/web-interface/view", params={"bvid": "BV1"})


@pytest.mark.parametrize("operation", ["favorite", "watch_later"])
async def test_native_save_propagates_view_application_error_without_post(
    operation: str,
) -> None:
    client = _authenticated_client()
    client._get_json = AsyncMock(side_effect=BilibiliAPIError("private view response", code=-400))
    post = AsyncMock(return_value={})
    client._post_json = post

    with pytest.raises(BilibiliAPIError) as captured:
        if operation == "favorite":
            await client.add_video_to_favorite("BV1", 7)
        else:
            await client.add_video_to_watch_later("BV1")

    assert captured.value.code == -400
    post.assert_not_awaited()


@pytest.mark.parametrize("invalid_aid", [None, 0, -1, False, True, "42"])
@pytest.mark.parametrize("operation", ["favorite", "watch_later"])
async def test_native_save_rejects_invalid_aid_without_post(
    invalid_aid: object,
    operation: str,
) -> None:
    client = _authenticated_client()
    client._get_json = AsyncMock(return_value={"aid": invalid_aid})
    post = AsyncMock(return_value={})
    client._post_json = post

    with pytest.raises(BilibiliAPIError, match="invalid video aid"):
        if operation == "favorite":
            await client.add_video_to_favorite("BV1", 7)
        else:
            await client.add_video_to_watch_later("BV1")

    post.assert_not_awaited()


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


async def test_ensure_favorite_folder_serializes_same_title_creation() -> None:
    client = _authenticated_client()
    folders: list[FavoriteFolder] = []

    async def list_folders() -> list[FavoriteFolder]:
        return list(folders)

    async def create_folder(path: str, *, data: dict[str, object]) -> dict[str, object]:
        assert path == "/x/v3/fav/folder/add"
        assert data["title"] == "OpenBiliClaw"
        await asyncio.sleep(0)
        folders.append(FavoriteFolder(media_id=9, title="OpenBiliClaw"))
        return {"id": 9}

    client.get_favorite_folders = AsyncMock(side_effect=list_folders)
    client._post_json = AsyncMock(side_effect=create_folder)

    first, second = await asyncio.gather(
        client.ensure_favorite_folder("OpenBiliClaw"),
        client.ensure_favorite_folder("OpenBiliClaw"),
    )

    assert first == FavoriteFolder(media_id=9, title="OpenBiliClaw")
    assert second == first
    assert client.get_favorite_folders.await_count == 2
    assert client._post_json.await_count == 1


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
    resolve = AsyncMock(return_value=42)
    post = AsyncMock(return_value={})
    client._resolve_aid = resolve
    client._post_json = post

    with pytest.raises(BilibiliAuthExpiredError, match="login required"):
        await client.add_video_to_watch_later("BV1")

    resolve.assert_not_awaited()
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


@pytest.mark.parametrize(("status_code", "application_code"), [(412, -412), (429, -429)])
async def test_post_json_preserves_http_rate_limit_status_as_safe_code(
    status_code: int,
    application_code: int,
) -> None:
    def blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            request=request,
            text="Cookie=secret; csrf=secret; private response body",
        )

    client = _authenticated_client()
    old_http = client._client
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(blocked))
    await old_http.aclose()
    try:
        with pytest.raises(BilibiliAPIError) as captured:
            await client._post_json("/write", data={"csrf": "csrf"})
    finally:
        await client.close()

    assert captured.value.code == application_code
    assert str(application_code) in str(captured.value)
    assert isinstance(captured.value.__cause__, httpx.HTTPStatusError)
    assert "secret" not in str(captured.value.__cause__)
    assert "secret" not in str(captured.value)
    assert "private response body" not in str(captured.value)


@pytest.mark.parametrize(("status_code", "application_code"), [(412, -412), (429, -429)])
async def test_get_json_preserves_http_rate_limit_status_as_safe_code(
    status_code: int,
    application_code: int,
) -> None:
    def blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            request=request,
            text="Cookie=secret; csrf=secret; private response body",
        )

    client = _authenticated_client()
    old_http = client._client
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(blocked))
    await old_http.aclose()
    try:
        with pytest.raises(BilibiliAPIError) as captured:
            await client._get_json("/x/web-interface/view", params={"bvid": "BV1"})
    finally:
        await client.close()

    assert captured.value.code == application_code
    assert str(application_code) in str(captured.value)
    assert isinstance(captured.value.__cause__, httpx.HTTPStatusError)
    assert "secret" not in str(captured.value.__cause__)
    assert "secret" not in str(captured.value)
    assert "private response body" not in str(captured.value)


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
