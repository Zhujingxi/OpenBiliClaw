from __future__ import annotations

import asyncio
from types import SimpleNamespace
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
from openbiliclaw.saved_sync.adapters.bilibili import BilibiliNativeSaveAdapter
from openbiliclaw.saved_sync.models import NativeSaveRoute, SavedItemInput


def _route(action: str) -> NativeSaveRoute:
    if action == "favorite":
        return NativeSaveRoute("favorite", "favorite", "B站 OpenBiliClaw 收藏夹")
    return NativeSaveRoute("watch_later", "watch_later", "B站稍后再看")


def _client() -> SimpleNamespace:
    return SimpleNamespace(
        ensure_favorite_folder=AsyncMock(
            return_value=FavoriteFolder(media_id=7, title="OpenBiliClaw")
        ),
        add_video_to_favorite=AsyncMock(return_value=None),
        add_video_to_watch_later=AsyncMock(return_value=None),
    )


def test_bilibili_adapter_declares_native_save_capability_and_labels() -> None:
    adapter = BilibiliNativeSaveAdapter(_client())

    assert adapter.capability.platform == "bilibili"
    assert adapter.capability.supports_favorite is True
    assert adapter.capability.supports_watch_later is True
    assert adapter.capability.supports_named_collection is True
    assert adapter.capability.requires_extension is False
    assert adapter.target_label("favorite") == "B站 OpenBiliClaw 收藏夹"
    assert adapter.target_label("watch_later") == "B站稍后再看"


async def test_favorite_creates_openbiliclaw_folder_then_adds_video() -> None:
    client = _client()
    adapter = BilibiliNativeSaveAdapter(client)

    result = await adapter.save(
        SavedItemInput("bilibili", "BV1"),
        _route("favorite"),
    )

    client.ensure_favorite_folder.assert_awaited_once_with("OpenBiliClaw")
    client.add_video_to_favorite.assert_awaited_once_with("BV1", 7)
    client.add_video_to_watch_later.assert_not_awaited()
    assert result.status == "synced"
    assert result.item_key == "bilibili:BV1"


async def test_watch_later_uses_native_watch_later() -> None:
    client = _client()
    adapter = BilibiliNativeSaveAdapter(client)

    result = await adapter.save(
        SavedItemInput("bilibili", "BV1"),
        _route("watch_later"),
    )

    client.add_video_to_watch_later.assert_awaited_once_with("BV1")
    client.ensure_favorite_folder.assert_not_awaited()
    client.add_video_to_favorite.assert_not_awaited()
    assert result.status == "synced"


@pytest.mark.parametrize("action", ["favorite", "watch_later"])
async def test_expired_auth_maps_to_login_required(action: str) -> None:
    client = _client()
    if action == "favorite":
        client.ensure_favorite_folder.side_effect = BilibiliAuthExpiredError(
            "private Cookie response", code=-101
        )
    else:
        client.add_video_to_watch_later.side_effect = BilibiliAuthExpiredError(
            "private Cookie response", code=-101
        )

    result = await BilibiliNativeSaveAdapter(client).save(
        SavedItemInput("bilibili", "BV1"),
        _route(action),
    )

    assert result.status == "login_required"
    assert result.error_code == "bilibili_-101"
    assert "private" not in result.error_message


async def test_generic_api_minus_101_defensively_maps_to_login_required() -> None:
    client = _client()
    client.add_video_to_watch_later.side_effect = BilibiliAPIError(
        "Cookie=secret; private response", code=-101
    )

    result = await BilibiliNativeSaveAdapter(client).save(
        SavedItemInput("bilibili", "BV1"),
        _route("watch_later"),
    )

    assert result.status == "login_required"
    assert result.error_code == "bilibili_-101"
    assert result.error_message == "Bilibili login required"


async def test_favorite_duplicate_code_maps_to_already_synced() -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=s; bili_jct=csrf; DedeUserID=1")
    client.ensure_favorite_folder = AsyncMock(
        return_value=FavoriteFolder(media_id=7, title="OpenBiliClaw")
    )
    client._resolve_aid = AsyncMock(return_value=42)
    client._post_json = AsyncMock(side_effect=BilibiliAPIError("private response", code=11201))
    try:
        result = await BilibiliNativeSaveAdapter(client).save(
            SavedItemInput("bilibili", "BV1"),
            _route("favorite"),
        )
    finally:
        await client.close()

    assert result.status == "already_synced"
    assert result.error_code == "bilibili_11201"
    assert "private" not in result.error_message


@pytest.mark.parametrize("boundary", ["folder", "resolver"])
async def test_generic_11201_outside_resource_deal_is_failed(boundary: str) -> None:
    client = BilibiliAPIClient(cookie="SESSDATA=s; bili_jct=csrf; DedeUserID=1")
    post = AsyncMock(return_value={})
    client._post_json = post
    if boundary == "folder":
        client.get_favorite_folders = AsyncMock(
            side_effect=BilibiliAPIError("private folder response", code=11201)
        )
    else:
        client.ensure_favorite_folder = AsyncMock(
            return_value=FavoriteFolder(media_id=7, title="OpenBiliClaw")
        )
        client._get_json = AsyncMock(
            side_effect=BilibiliAPIError("private resolver response", code=11201)
        )

    try:
        result = await BilibiliNativeSaveAdapter(client).save(
            SavedItemInput("bilibili", "BV1"),
            _route("favorite"),
        )
    finally:
        await client.close()

    assert result.status == "failed"
    assert result.error_code == "bilibili_11201"
    assert result.error_message == "Bilibili native save failed (code 11201)"
    post.assert_not_awaited()


async def test_duplicate_exception_on_non_favorite_route_is_failed() -> None:
    client = _client()
    client.add_video_to_watch_later.side_effect = BilibiliFavoriteDuplicateError(
        "Bilibili favorite already contains this video (code 11201)",
        code=11201,
    )

    result = await BilibiliNativeSaveAdapter(client).save(
        SavedItemInput("bilibili", "BV1"),
        _route("watch_later"),
    )

    assert result.status == "failed"
    assert result.error_code == "bilibili_11201"
    assert result.error_message == "Bilibili native save failed (code 11201)"


async def test_watch_later_deleted_video_code_maps_to_sanitized_failure() -> None:
    client = _client()
    client.add_video_to_watch_later.side_effect = BilibiliAPIError(
        "Cookie=secret; private deleted-video response", code=90003
    )

    result = await BilibiliNativeSaveAdapter(client).save(
        SavedItemInput("bilibili", "BV1"),
        _route("watch_later"),
    )

    assert result.status == "failed"
    assert result.error_code == "bilibili_video_unavailable"
    assert result.error_message == "Bilibili video is unavailable for watch later"


@pytest.mark.parametrize("code", [-352, -412, -429, -509])
async def test_rate_control_codes_map_to_rate_limited(code: int) -> None:
    client = _client()
    client.add_video_to_watch_later.side_effect = BilibiliAPIError("private response", code=code)

    result = await BilibiliNativeSaveAdapter(client).save(
        SavedItemInput("bilibili", "BV1"),
        _route("watch_later"),
    )

    assert result.status == "rate_limited"
    assert result.error_code == f"bilibili_{code}"
    assert str(code) in result.error_message
    assert "private" not in result.error_message


@pytest.mark.parametrize(
    ("action", "status_code", "application_code"),
    [("watch_later", 412, -412), ("favorite", 429, -429)],
)
async def test_get_http_rate_limit_maps_to_adapter_rate_limited(
    action: str,
    status_code: int,
    application_code: int,
) -> None:
    def blocked(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            status_code,
            request=request,
            text="Cookie=secret; csrf=secret; private response body",
        )

    client = BilibiliAPIClient(cookie="SESSDATA=s; bili_jct=csrf; DedeUserID=1")
    old_http = client._client
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(blocked))
    await old_http.aclose()
    try:
        result = await BilibiliNativeSaveAdapter(client).save(
            SavedItemInput("bilibili", "BV1"),
            _route(action),
        )
    finally:
        await client.close()

    assert result.status == "rate_limited"
    assert result.error_code == f"bilibili_{application_code}"
    assert "secret" not in result.error_message
    assert "private response body" not in result.error_message


async def test_other_api_failure_is_sanitized() -> None:
    client = _client()
    client.add_video_to_watch_later.side_effect = BilibiliAPIError(
        "Cookie=secret; csrf=secret; private response", code=12345
    )

    result = await BilibiliNativeSaveAdapter(client).save(
        SavedItemInput("bilibili", "BV1"),
        _route("watch_later"),
    )

    assert result.status == "failed"
    assert result.error_code == "bilibili_12345"
    assert result.error_message == "Bilibili native save failed (code 12345)"


async def test_unexpected_client_failure_is_sanitized() -> None:
    client = _client()
    client.add_video_to_watch_later.side_effect = RuntimeError(
        "Cookie=secret; csrf=secret; private response"
    )

    result = await BilibiliNativeSaveAdapter(client).save(
        SavedItemInput("bilibili", "BV1"),
        _route("watch_later"),
    )

    assert result.status == "failed"
    assert result.error_code == "bilibili_native_save_failed"
    assert result.error_message == "Bilibili native save failed"


async def test_adapter_propagates_cancellation() -> None:
    client = _client()
    client.add_video_to_watch_later.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await BilibiliNativeSaveAdapter(client).save(
            SavedItemInput("bilibili", "BV1"),
            _route("watch_later"),
        )
