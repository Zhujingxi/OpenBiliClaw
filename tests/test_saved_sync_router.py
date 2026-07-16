from __future__ import annotations

from typing import cast

import pytest

from openbiliclaw.saved_sync.models import (
    NativeSaveAction,
    NativeSaveCapability,
    NativeSaveResult,
    NativeSaveRoute,
    NativeSaveStatus,
    SavedItemInput,
)
from openbiliclaw.saved_sync.router import NativeSaveRouter


class FakeAdapter:
    def __init__(self, capability: NativeSaveCapability, result_status: str) -> None:
        self.capability = capability
        self.result_status = result_status

    def target_label(self, action: NativeSaveAction) -> str:
        if self.capability.platform == "reddit":
            return "Reddit Saved"
        return "B站稍后观看" if action == "watch_later" else "B站 OpenBiliClaw 收藏夹"

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        return NativeSaveResult(
            item_key=item.item_key,
            status=cast("NativeSaveStatus", self.result_status),
            resolved_action=route.resolved_action,
            resolved_target=route.resolved_target,
        )


def test_watch_later_falls_back_to_favorite() -> None:
    adapter = FakeAdapter(
        NativeSaveCapability("reddit", True, False, False),
        result_status="synced",
    )

    routed_adapter, route = NativeSaveRouter([adapter]).route("reddit", "watch_later")

    assert routed_adapter is adapter
    assert route.requested_action == "watch_later"
    assert route.resolved_action == "favorite"
    assert route.resolved_target == "Reddit Saved"


def test_watch_later_uses_native_action_when_supported() -> None:
    adapter = FakeAdapter(
        NativeSaveCapability("bilibili", True, True, True),
        result_status="synced",
    )

    _, route = NativeSaveRouter([adapter]).route("bilibili", "watch_later")

    assert route.resolved_action == "watch_later"
    assert route.resolved_target == "B站稍后观看"


def test_register_canonicalizes_platform_aliases() -> None:
    adapter = FakeAdapter(
        NativeSaveCapability("twitter", True, False, False),
        result_status="synced",
    )
    router = NativeSaveRouter()

    router.register(adapter)

    assert router.route("x", "favorite")[0] is adapter


@pytest.mark.parametrize(
    ("adapter", "platform", "action"),
    [
        (None, "youtube", "favorite"),
        (
            FakeAdapter(NativeSaveCapability("reddit", False, False, False), "unsupported"),
            "reddit",
            "favorite",
        ),
        (
            FakeAdapter(NativeSaveCapability("reddit", False, False, False), "unsupported"),
            "reddit",
            "watch_later",
        ),
    ],
)
def test_router_rejects_unsupported_routes(
    adapter: FakeAdapter | None,
    platform: str,
    action: NativeSaveAction,
) -> None:
    router = NativeSaveRouter([adapter] if adapter is not None else None)

    with pytest.raises(ValueError, match="unsupported"):
        router.route(platform, action)
