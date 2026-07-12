from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.saved_sync.adapters.extension import (
    ExtensionNativeSaveAdapter,
    build_extension_native_save_adapters,
)
from openbiliclaw.saved_sync.models import NativeSaveAction, SavedItemInput
from openbiliclaw.saved_sync.router import NativeSaveRouter


@pytest.fixture
def broker() -> AsyncMock:
    return AsyncMock()


@pytest.mark.parametrize(
    ("platform", "intent", "resolved", "target"),
    [
        ("youtube", "favorite", "favorite", "OpenBiliClaw"),
        ("youtube", "watch_later", "watch_later", "YouTube Watch Later"),
        ("xiaohongshu", "watch_later", "favorite", "小红书收藏"),
        ("douyin", "watch_later", "favorite", "抖音收藏"),
        ("twitter", "watch_later", "favorite", "X Bookmarks"),
        ("zhihu", "watch_later", "favorite", "OpenBiliClaw"),
        ("reddit", "favorite", "favorite", "Reddit Saved"),
    ],
)
def test_extension_adapter_route_matrix(
    platform: str,
    intent: str,
    resolved: str,
    target: str,
    broker: AsyncMock,
) -> None:
    router = NativeSaveRouter(build_extension_native_save_adapters(broker))

    adapter, route = router.route(platform, cast("NativeSaveAction", intent))

    assert adapter.capability.requires_extension is True
    assert route.resolved_action == resolved
    assert route.resolved_target == target


def test_extension_adapter_factory_is_ordered_and_immutable(broker: AsyncMock) -> None:
    adapters = build_extension_native_save_adapters(broker)

    assert isinstance(adapters, tuple)
    assert [adapter.capability.platform for adapter in adapters] == [
        "youtube",
        "xiaohongshu",
        "douyin",
        "twitter",
        "zhihu",
        "reddit",
    ]
    assert [adapter.capability.supports_named_collection for adapter in adapters] == [
        True,
        False,
        False,
        False,
        True,
        False,
    ]


async def test_extension_adapter_delegates_to_broker(broker: AsyncMock) -> None:
    broker.save.return_value = object()
    adapter = build_extension_native_save_adapters(broker)[0]
    item = SavedItemInput("youtube", "video-1")
    _, route = NativeSaveRouter([adapter]).route("youtube", "favorite")

    result = await adapter.save(item, route)

    assert result is broker.save.return_value
    broker.save.assert_awaited_once_with(item, route)


async def test_extension_adapter_rejects_cross_platform_item_before_enqueue(
    broker: AsyncMock,
) -> None:
    adapter: ExtensionNativeSaveAdapter = build_extension_native_save_adapters(broker)[0]
    item = SavedItemInput("reddit", "post-1")
    _, route = NativeSaveRouter([adapter]).route("youtube", "favorite")

    with pytest.raises(ValueError, match="platform does not match"):
        await adapter.save(item, route)

    broker.save.assert_not_awaited()
