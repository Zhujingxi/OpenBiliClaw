from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.bilibili.api import (
    BilibiliAPIError,
    BilibiliAuthExpiredError,
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


@pytest.mark.parametrize(
    ("action", "code"),
    [("watch_later", 90003), ("favorite", 11201)],
)
async def test_duplicate_application_codes_map_to_already_synced(
    action: str,
    code: int,
) -> None:
    client = _client()
    if action == "favorite":
        client.add_video_to_favorite.side_effect = BilibiliAPIError("private response", code=code)
    else:
        client.add_video_to_watch_later.side_effect = BilibiliAPIError(
            "private response", code=code
        )

    result = await BilibiliNativeSaveAdapter(client).save(
        SavedItemInput("bilibili", "BV1"),
        _route(action),
    )

    assert result.status == "already_synced"
    assert result.error_code == f"bilibili_{code}"
    assert "private" not in result.error_message


@pytest.mark.parametrize("code", [-352, -412, -509])
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
