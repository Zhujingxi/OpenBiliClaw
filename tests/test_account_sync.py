from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from openbiliclaw.bilibili.api import FavoriteFolder, FavoriteFolderWithItems, FollowingUser


class _FakeMemoryManager:
    def __init__(self, state: dict[str, object] | None = None) -> None:
        self.state = state or {
            "last_history_view_at": 0,
            "last_history_bvid": "",
            "last_favorites_sync_at": "",
            "favorite_signature": "",
            "last_following_sync_at": "",
            "following_signature": "",
            "last_account_sync_at": "",
            "last_sync_error": "",
        }
        self.events: list[dict[str, Any]] = []

    def load_account_sync_state(self) -> dict[str, object]:
        return dict(self.state)

    def save_account_sync_state(self, state: dict[str, object]) -> None:
        self.state = dict(state)

    async def propagate_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _FakeSoulEngine:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    async def analyze_events(self, events: list[dict[str, Any]]) -> None:
        self.calls.append(events)


@dataclass
class _FakeClient:
    history_items: list[dict[str, Any]]
    favorites: list[FavoriteFolderWithItems]
    following: list[FollowingUser]
    fail_history: bool = False
    fail_favorites: bool = False
    fail_following: bool = False

    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]:
        if self.fail_history:
            raise RuntimeError("history boom")
        return self.history_items[:max_items]

    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
    ) -> list[FavoriteFolderWithItems]:
        if self.fail_favorites:
            raise RuntimeError("favorites boom")
        return self.favorites[:max_folders]

    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[FollowingUser]:
        if self.fail_following:
            raise RuntimeError("following boom")
        return self.following[:page_size]


def _history_item(bvid: str, view_at: int, title: str = "视频") -> dict[str, Any]:
    return {
        "title": title,
        "author": "UP主",
        "history": {
            "bvid": bvid,
            "view_at": view_at,
        },
    }


def _favorite_item(bvid: str, title: str = "收藏视频") -> dict[str, Any]:
    return {
        "bvid": bvid,
        "title": title,
        "upper": {"name": "收藏UP"},
    }


def _favorite_folder_with_items(folder_id: int, *bvids: str) -> FavoriteFolderWithItems:
    return FavoriteFolderWithItems(
        folder=FavoriteFolder(
            media_id=folder_id,
            title=f"folder-{folder_id}",
            media_count=len(bvids),
        ),
        items=[_favorite_item(bvid) for bvid in bvids],
        truncated=False,
    )


@pytest.mark.asyncio
async def test_account_sync_imports_incremental_history_only() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager(
        {
            "last_history_view_at": 100,
            "last_history_bvid": "BVOLD",
            "last_favorites_sync_at": "",
            "favorite_signature": "",
            "last_following_sync_at": "",
            "following_signature": "",
            "last_account_sync_at": "",
            "last_sync_error": "",
        }
    )
    soul = _FakeSoulEngine()
    client = _FakeClient(
        history_items=[
            _history_item("BVNEW2", 102, "更近的新视频"),
            _history_item("BVNEW1", 101, "新的视频"),
            _history_item("BVOLD", 100, "旧视频"),
        ],
        favorites=[],
        following=[],
    )

    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    result = await service.sync_now()

    assert result["synced"] is True
    assert result["new_event_count"] == 2
    assert [event["metadata"]["bvid"] for event in memory.events] == ["BVNEW2", "BVNEW1"]
    assert soul.calls and len(soul.calls[0]) == 2
    assert memory.state["last_history_view_at"] == 102
    assert memory.state["last_history_bvid"] == "BVNEW2"


@pytest.mark.asyncio
async def test_account_sync_skips_favorites_and_following_when_signature_unchanged() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    favorites = [_favorite_folder_with_items(1, "BVF1", "BVF2")]
    following = [FollowingUser(mid=1, uname="影视飓风"), FollowingUser(mid=2, uname="何同学")]
    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(
            {
                "last_history_view_at": 0,
                "last_history_bvid": "",
                "last_favorites_sync_at": "2026-03-14T12:00:00",
                "favorite_signature": "1:BVF1,BVF2",
                "last_following_sync_at": "2026-03-14T12:00:00",
                "following_signature": "1,2",
                "last_account_sync_at": "2026-03-14T12:00:00",
                "last_sync_error": "",
            }
        ),
        bilibili_client=_FakeClient(history_items=[], favorites=favorites, following=following),
        soul_engine=_FakeSoulEngine(),
    )

    result = await service.sync_now()

    assert result["synced"] is False
    assert result["new_event_count"] == 0
    assert service.memory_manager.events == []
    assert service.soul_engine.calls == []


@pytest.mark.asyncio
async def test_account_sync_imports_new_favorites_and_following() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    soul = _FakeSoulEngine()
    client = _FakeClient(
        history_items=[],
        favorites=[_favorite_folder_with_items(7, "BVFRESH")],
        following=[FollowingUser(mid=99, uname="半佛仙人")],
    )

    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    result = await service.sync_now()

    assert result["new_event_count"] == 2
    assert {event["event_type"] for event in memory.events} == {"favorite", "follow"}
    assert memory.state["favorite_signature"] == "7:BVFRESH"
    assert memory.state["following_signature"] == "99"


@pytest.mark.asyncio
async def test_account_sync_returns_partial_success_when_one_source_fails() -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    memory = _FakeMemoryManager()
    soul = _FakeSoulEngine()
    client = _FakeClient(
        history_items=[_history_item("BVOK", 101)],
        favorites=[],
        following=[FollowingUser(mid=7, uname="小约翰可汗")],
        fail_favorites=True,
    )

    service = AccountSyncService(memory_manager=memory, bilibili_client=client, soul_engine=soul)

    result = await service.sync_now()

    assert result["synced"] is True
    assert result["new_event_count"] == 2
    assert "favorites boom" in str(memory.state["last_sync_error"])
    assert {event["event_type"] for event in memory.events} == {"view", "follow"}


@pytest.mark.asyncio
async def test_account_sync_run_forever_recovers_from_iteration_error(caplog) -> None:
    from openbiliclaw.runtime.account_sync import AccountSyncService

    service = AccountSyncService(
        memory_manager=_FakeMemoryManager(),
        bilibili_client=_FakeClient(history_items=[], favorites=[], following=[]),
        soul_engine=_FakeSoulEngine(),
        check_interval_seconds=1,
    )

    async def _broken_sync_if_due() -> dict[str, object]:
        raise RuntimeError("boom")

    async def _cancel_sleep(_: int) -> None:
        raise asyncio.CancelledError

    service.sync_if_due = _broken_sync_if_due  # type: ignore[method-assign]

    original_sleep = asyncio.sleep
    try:
        asyncio.sleep = _cancel_sleep
        with pytest.raises(asyncio.CancelledError):
            await service.run_forever()
    finally:
        asyncio.sleep = original_sleep

    assert "Unexpected error in account sync loop" in caplog.text
