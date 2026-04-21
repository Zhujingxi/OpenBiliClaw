"""Periodic account-side sync for long-term Bilibili signals."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class SupportsAccountSyncState(Protocol):
    def load_account_sync_state(self) -> dict[str, object]: ...
    def save_account_sync_state(self, state: dict[str, object]) -> None: ...
    async def propagate_event(self, event: dict[str, Any]) -> None: ...


class SupportsAccountClient(Protocol):
    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]: ...
    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
    ) -> list[Any]: ...
    async def get_following(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> list[Any]: ...


class SupportsSoulAnalyzer(Protocol):
    async def analyze_events(self, events: list[dict[str, Any]]) -> None: ...


@dataclass
class AccountSyncService:
    """Incrementally import account-side history, favorites, and following."""

    memory_manager: SupportsAccountSyncState
    bilibili_client: SupportsAccountClient
    soul_engine: SupportsSoulAnalyzer
    sync_interval_hours: int = 6
    history_max_items: int = 200
    max_folders: int = 10
    max_items_per_folder: int = 50
    following_page_size: int = 100
    check_interval_seconds: int = 300

    async def sync_if_due(self) -> dict[str, object]:
        """Run one account sync only when the configured interval has elapsed."""
        state = self.memory_manager.load_account_sync_state()
        if not self._is_due(str(state.get("last_account_sync_at", ""))):
            return {
                "synced": False,
                "new_event_count": 0,
                "reason": "not_due",
            }
        return await self.sync_now()

    async def sync_now(self) -> dict[str, object]:
        """Run one immediate incremental account sync."""
        state = self.memory_manager.load_account_sync_state()
        events: list[dict[str, Any]] = []
        errors: list[str] = []

        try:
            history = await self.bilibili_client.get_user_history(max_items=self.history_max_items)
            new_history, last_view_at, last_bvid = self._filter_new_history(
                history,
                last_view_at=self._to_int(state.get("last_history_view_at", 0)),
                last_bvid=str(state.get("last_history_bvid", "")),
            )
            events.extend(self._history_events(new_history))
            state["last_history_view_at"] = last_view_at
            state["last_history_bvid"] = last_bvid
        except Exception as exc:
            errors.append(str(exc))

        try:
            favorites = await self.bilibili_client.get_all_favorites(
                max_folders=self.max_folders,
                max_items_per_folder=self.max_items_per_folder,
            )
            current_signature = self._favorite_signature(favorites)
            previous_signature = str(state.get("favorite_signature", ""))
            if current_signature and current_signature != previous_signature:
                events.extend(self._favorite_events(favorites))
                state["favorite_signature"] = current_signature
                state["last_favorites_sync_at"] = self._now().isoformat()
        except Exception as exc:
            errors.append(str(exc))

        try:
            following = await self.bilibili_client.get_following(
                page=1,
                page_size=self.following_page_size,
            )
            current_signature = self._following_signature(following)
            previous_signature = str(state.get("following_signature", ""))
            if current_signature and current_signature != previous_signature:
                events.extend(self._following_events(following))
                state["following_signature"] = current_signature
                state["last_following_sync_at"] = self._now().isoformat()
        except Exception as exc:
            errors.append(str(exc))

        if events:
            for event in events:
                await self.memory_manager.propagate_event(event)
            await self.soul_engine.analyze_events(events)

        state["last_account_sync_at"] = self._now().isoformat()
        state["last_sync_error"] = " | ".join(errors)
        self.memory_manager.save_account_sync_state(state)
        return {
            "synced": bool(events),
            "new_event_count": len(events),
            "errors": errors,
        }

    def get_runtime_status(self) -> dict[str, object]:
        """Expose lightweight account sync runtime fields."""
        state = self.memory_manager.load_account_sync_state()
        return {
            "last_account_sync_at": str(state.get("last_account_sync_at", "")),
            "last_account_sync_error": str(state.get("last_sync_error", "")),
        }

    async def run_forever(self) -> None:
        """Run account sync loop until cancelled."""
        while True:
            try:
                await self.sync_if_due()
            except Exception:
                logger.exception("Unexpected error in account sync loop")
            await asyncio.sleep(self.check_interval_seconds)

    def _filter_new_history(
        self,
        items: list[dict[str, Any]],
        *,
        last_view_at: int,
        last_bvid: str,
    ) -> tuple[list[dict[str, Any]], int, str]:
        newest_view_at = last_view_at
        newest_bvid = last_bvid
        accepted: list[dict[str, Any]] = []
        for item in items:
            history_meta = item.get("history", {})
            if not isinstance(history_meta, dict):
                history_meta = {}
            view_at = self._to_int(history_meta.get("view_at", item.get("view_at", 0)))
            bvid = str(history_meta.get("bvid", "")).strip()
            if view_at < last_view_at:
                continue
            if view_at == last_view_at and bvid and bvid == last_bvid:
                continue
            accepted.append(item)
            if view_at > newest_view_at:
                newest_view_at = view_at
                newest_bvid = bvid
            elif view_at == newest_view_at and bvid:
                newest_bvid = bvid
        return accepted, newest_view_at, newest_bvid

    def _history_events(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for item in items:
            history_meta = item.get("history", {})
            if not isinstance(history_meta, dict):
                history_meta = {}
            bvid = str(history_meta.get("bvid", "")).strip()
            events.append(
                {
                    "event_type": "view",
                    "title": str(item.get("title", "")).strip(),
                    "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                    "metadata": {
                        "bvid": bvid,
                        "author": str(item.get("author", "")).strip(),
                        "view_at": self._to_int(
                            history_meta.get("view_at", item.get("view_at", 0))
                        ),
                        "source": "account_sync",
                    },
                }
            )
        return events

    def _favorite_signature(self, folders: list[Any]) -> str:
        parts: list[str] = []
        for folder in folders:
            folder_id = str(getattr(getattr(folder, "folder", None), "media_id", ""))
            item_ids = [
                str(item.get("bvid", "")).strip()
                for item in getattr(folder, "items", [])
                if isinstance(item, dict) and str(item.get("bvid", "")).strip()
            ]
            if folder_id and item_ids:
                parts.append(f"{folder_id}:{','.join(item_ids)}")
        return "|".join(parts)

    def _favorite_events(self, folders: list[Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for folder in folders:
            folder_obj = getattr(folder, "folder", None)
            folder_title = str(getattr(folder_obj, "title", "")).strip()
            folder_id = int(getattr(folder_obj, "media_id", 0) or 0)
            for item in getattr(folder, "items", []):
                if not isinstance(item, dict):
                    continue
                bvid = str(item.get("bvid", "")).strip()
                upper = item.get("upper", {})
                if not isinstance(upper, dict):
                    upper = {}
                events.append(
                    {
                        "event_type": "favorite",
                        "title": str(item.get("title", "")).strip(),
                        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                        "metadata": {
                            "bvid": bvid,
                            "folder_id": folder_id,
                            "folder_title": folder_title,
                            "up_name": str(upper.get("name", "")).strip(),
                            "source": "account_sync",
                        },
                    }
                )
        return events

    def _following_signature(self, following: list[Any]) -> str:
        mids = sorted(
            str(getattr(user, "mid", "")).strip()
            for user in following
            if str(getattr(user, "mid", "")).strip()
        )
        return ",".join(mids)

    def _following_events(self, following: list[Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for user in following:
            mid = int(getattr(user, "mid", 0) or 0)
            uname = str(getattr(user, "uname", "")).strip()
            events.append(
                {
                    "event_type": "follow",
                    "title": uname,
                    "url": f"https://space.bilibili.com/{mid}" if mid else "",
                    "metadata": {
                        "up_mid": mid,
                        "up_name": uname,
                        "sign": str(getattr(user, "sign", "")).strip(),
                        "source": "account_sync",
                    },
                }
            )
        return events

    def _is_due(self, last_sync_at: str) -> bool:
        parsed = self._parse_iso_datetime(last_sync_at)
        if parsed is None:
            return True
        return self._now() - parsed >= timedelta(hours=self.sync_interval_hours)

    def _parse_iso_datetime(self, value: str) -> datetime | None:
        if not value:
            return None
        with_timezone = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(with_timezone)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _now(self) -> datetime:
        return datetime.now(tz=UTC)

    @staticmethod
    def _to_int(value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0
