"""Continuous refresh controller for the local API runtime."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol


class SupportsRuntimeState(Protocol):
    def load_discovery_runtime_state(self) -> dict[str, object]: ...
    def save_discovery_runtime_state(self, state: dict[str, object]) -> None: ...
    def get_layer(self, name: str) -> Any: ...


class SupportsEventDatabase(Protocol):
    def query_events_since(
        self,
        *,
        after_event_id: int,
        event_types: list[str],
    ) -> list[dict[str, Any]]: ...
    def get_latest_event_id(self) -> int: ...
    def count_recommendations(self) -> int: ...
    def count_unread_recommendations(self) -> int: ...
    def count_pool_candidates(self) -> int: ...
    def get_notification_candidate(
        self,
        *,
        min_confidence: float = 0.82,
    ) -> dict[str, Any] | None: ...
    def mark_notification_sent(self, bvid: str) -> None: ...


class SupportsProfileEngine(Protocol):
    async def get_profile(self) -> Any: ...


class SupportsDiscoveryEngine(Protocol):
    async def discover(
        self,
        profile: Any,
        strategies: list[str] | None = None,
        limit: int = 30,
    ) -> list[Any]: ...


class SupportsRecommendationEngine(Protocol):
    async def generate_recommendations(
        self,
        discovered: list[Any] | None,
        profile: Any,
        limit: int = 10,
    ) -> list[Any]: ...


@dataclass
class ContinuousRefreshController:
    """Keep discovery cache and recommendations fresh during API runtime."""

    memory_manager: SupportsRuntimeState
    database: SupportsEventDatabase
    soul_engine: SupportsProfileEngine
    discovery_engine: SupportsDiscoveryEngine
    recommendation_engine: SupportsRecommendationEngine
    event_hub: Any | None = None
    signal_event_threshold: int = 6
    event_refresh_minutes: int = 0
    trending_refresh_hours: int = 3
    explore_refresh_hours: int = 12
    notification_cooldown_hours: int = 2
    check_interval_seconds: int = 60
    discovery_limit: int = 18
    pool_target_count: int = 30
    _manual_refresh_task: asyncio.Task[None] | None = None
    _manual_refresh_state: str = "idle"
    _manual_refresh_message: str = ""
    _manual_refresh_started_at: str = ""
    _manual_refresh_finished_at: str = ""

    _signal_event_types = [
        "view",
        "search",
        "favorite",
        "like",
        "coin",
        "comment",
        "feedback",
    ]

    def get_runtime_status(self) -> dict[str, object]:
        """Build a lightweight runtime summary for popup or diagnostics."""
        state = self.memory_manager.load_discovery_runtime_state()
        refresh_values = [
            str(state.get("last_event_refresh_at", "")),
            str(state.get("last_trending_refresh_at", "")),
            str(state.get("last_explore_refresh_at", "")),
        ]
        parsed_refresh_values: list[datetime] = []
        for value in refresh_values:
            parsed = self._parse_iso_datetime(value)
            if parsed is not None:
                parsed_refresh_values.append(parsed)
        last_refresh_at = (
            max(parsed_refresh_values).isoformat() if parsed_refresh_values else ""
        )
        return {
            "initialized": self._is_initialized(),
            "recommendation_count": self.database.count_recommendations(),
            "pending_signal_events": self._pending_signal_events_count(state),
            "last_refresh_at": last_refresh_at,
            "last_notification_at": str(state.get("last_notification_at", "")),
            "unread_count": self.database.count_unread_recommendations(),
            "pool_available_count": self.database.count_pool_candidates(),
            "pool_target_count": self.pool_target_count,
            "last_replenished_count": self._int_state_value(
                state, "last_replenished_count"
            ),
            "recent_pool_topics": self._list_state_value(state, "recent_pool_topics"),
            "manual_refresh_state": self._manual_refresh_state,
            "manual_refresh_message": self._manual_refresh_message,
        }

    async def refresh_if_needed(self) -> dict[str, object]:
        """Refresh candidates and recommendations when thresholds are met."""
        state = self.memory_manager.load_discovery_runtime_state()
        if not self._is_initialized():
            return {"refreshed": False, "strategies": [], "reason": "not_initialized"}

        profile = await self.soul_engine.get_profile()
        plan = self._build_refresh_plan(state)
        if not plan:
            return {"refreshed": False, "strategies": [], "reason": "below_threshold"}

        return await self._run_refresh_plan(
            state=state,
            profile=profile,
            plan=plan,
            reason="triggered",
        )

    async def force_refresh(self) -> dict[str, object]:
        """Run a full refresh immediately, bypassing runtime thresholds."""
        state = self.memory_manager.load_discovery_runtime_state()
        if not self._is_initialized():
            return {"refreshed": False, "strategies": [], "reason": "not_initialized"}

        profile = await self.soul_engine.get_profile()
        plan = [
            ["search", "related_chain"],
            ["trending"],
            ["explore"],
        ]
        return await self._run_refresh_plan(
            state=state,
            profile=profile,
            plan=plan,
            reason="manual",
        )

    async def trigger_manual_refresh(self) -> dict[str, object]:
        """Schedule one background manual refresh without blocking the caller."""
        if not self._is_initialized():
            return {"accepted": False, "state": "idle", "reason": "not_initialized"}
        if self._manual_refresh_task is not None and not self._manual_refresh_task.done():
            return {"accepted": True, "state": "running", "reason": "already_running"}

        self._manual_refresh_state = "running"
        self._manual_refresh_message = "正在补货…"
        self._manual_refresh_started_at = self._now().isoformat()
        self._manual_refresh_finished_at = ""
        self._manual_refresh_task = asyncio.create_task(self._complete_manual_refresh())
        return {"accepted": True, "state": "running", "reason": "started"}

    def get_pending_notification(self) -> dict[str, object] | None:
        """Return one recommendation candidate for browser notification."""
        state = self.memory_manager.load_discovery_runtime_state()
        last_notification_at = self._parse_iso_datetime(
            str(state.get("last_notification_at", ""))
        )
        if last_notification_at is not None and self._now() - last_notification_at < timedelta(
            hours=self.notification_cooldown_hours
        ):
            return None
        candidate = self.database.get_notification_candidate(min_confidence=0.82)
        if candidate is None:
            return None
        return {
            "recommendation_id": int(candidate["id"]),
            "bvid": str(candidate.get("bvid", "")),
            "title": str(candidate.get("title", "")),
            "reason": str(candidate.get("expression", "")),
        }

    def mark_notification_sent(self, bvid: str) -> None:
        """Persist notification delivery markers."""
        self.database.mark_notification_sent(bvid)
        state = self.memory_manager.load_discovery_runtime_state()
        state["last_notification_at"] = self._now().isoformat()
        self.memory_manager.save_discovery_runtime_state(state)

    async def run_forever(self) -> None:
        """Run the refresh loop until cancelled."""
        while True:
            with suppress(Exception):
                await self.refresh_if_needed()
            await asyncio.sleep(self.check_interval_seconds)

    def _pending_signal_events_count(self, state: dict[str, object]) -> int:
        return len(
            self.database.query_events_since(
                after_event_id=self._int_state_value(state, "last_processed_event_id"),
                event_types=self._signal_event_types,
            )
        )

    def _build_refresh_plan(self, state: dict[str, object]) -> list[list[str]]:
        pending_events = self._pending_signal_events_count(state)
        pool_available = self.database.count_pool_candidates()
        pool_below_target = pool_available < self.pool_target_count

        if pool_below_target:
            return [
                ["search", "related_chain"],
                ["trending"],
                ["explore"],
            ]

        plan: list[list[str]] = []
        if pending_events >= self.signal_event_threshold:
            plan.append(["search", "related_chain"])
        if self._is_due(
            str(state.get("last_trending_refresh_at", "")),
            hours=self.trending_refresh_hours,
        ):
            plan.append(["trending"])
        if self._is_due(
            str(state.get("last_explore_refresh_at", "")),
            hours=self.explore_refresh_hours,
        ):
            plan.append(["explore"])
        return plan

    async def refresh_after_event_ingest(self) -> dict[str, object]:
        """Opportunistically refresh after new events arrive."""
        return await self.refresh_if_needed()

    async def refresh_after_feedback(self) -> dict[str, object]:
        """Opportunistically refresh after explicit feedback."""
        return await self.refresh_if_needed()

    async def refresh_after_init(self) -> dict[str, object]:
        """Allow callers to trigger a refresh immediately after initialization."""
        return await self.refresh_if_needed()

    async def _complete_manual_refresh(self) -> None:
        try:
            await self.force_refresh()
        except Exception as exc:
            self._manual_refresh_state = "failed"
            self._manual_refresh_message = f"这次补货没跑通：{exc}"
            self._manual_refresh_finished_at = self._now().isoformat()
            await self._publish_event(
                {
                    "type": "refresh.failed",
                    "phase": "failed",
                    "message": self._manual_refresh_message,
                    "pool_available_count": self.database.count_pool_candidates(),
                }
            )
            return
        self._manual_refresh_state = "success"
        self._manual_refresh_message = "刚给你补了一批新的。"
        self._manual_refresh_finished_at = self._now().isoformat()
        await self._publish_event(
            {
                "type": "refresh.pool_updated",
                "phase": "done",
                "message": self._manual_refresh_message,
                "pool_available_count": self.database.count_pool_candidates(),
            }
        )

    async def _run_refresh_plan(
        self,
        *,
        state: dict[str, object],
        profile: Any,
        plan: list[list[str]],
        reason: str,
    ) -> dict[str, object]:
        before_pool_count = self.database.count_pool_candidates()
        initial_pool_below_target = before_pool_count < self.pool_target_count
        all_discovered: list[Any] = []
        flattened_strategies: list[str] = []
        replenished_topics: list[str] = []

        await self._publish_event(
            {
                "type": "refresh.started",
                "phase": "running",
                "message": "开始给你补候选了",
                "pool_available_count": before_pool_count,
            }
        )

        for strategies in plan:
            current_pool_count = self.database.count_pool_candidates()
            if initial_pool_below_target and current_pool_count >= self.pool_target_count:
                break

            await self._publish_event(
                {
                    "type": "refresh.strategy",
                    "phase": "running",
                    "strategy": "+".join(strategies),
                    "message": self._strategy_message(strategies),
                    "pool_available_count": current_pool_count,
                }
            )

            discovered = await self.discovery_engine.discover(
                profile,
                strategies=strategies,
                limit=max(self.discovery_limit, self.pool_target_count - current_pool_count),
            )
            all_discovered.extend(discovered)
            flattened_strategies.extend(strategies)

            if discovered:
                replenished_topics.extend(self._extract_topics(discovered))

        recommendations = await self.recommendation_engine.generate_recommendations(
            all_discovered or None,
            profile,
            limit=10,
        )

        now = self._now().isoformat()
        latest_event_id = self.database.get_latest_event_id()
        if "search" in flattened_strategies or "related_chain" in flattened_strategies:
            state["last_event_refresh_at"] = now
            state["last_processed_event_id"] = latest_event_id
        if "trending" in flattened_strategies:
            state["last_trending_refresh_at"] = now
        if "explore" in flattened_strategies:
            state["last_explore_refresh_at"] = now
        after_pool_count = self.database.count_pool_candidates()
        state["last_replenished_count"] = max(0, after_pool_count - before_pool_count)
        if replenished_topics:
            state["recent_pool_topics"] = self._dedupe_topics(replenished_topics)[:3]
        self.memory_manager.save_discovery_runtime_state(state)
        await self._publish_event(
            {
                "type": "refresh.pool_updated",
                "phase": "done",
                "message": f"刚补进 {state['last_replenished_count']} 条新的",
                "pool_available_count": after_pool_count,
                "last_replenished_count": state["last_replenished_count"],
                "recent_pool_topics": self._list_state_value(state, "recent_pool_topics"),
            }
        )
        return {
            "refreshed": bool(flattened_strategies),
            "strategies": flattened_strategies,
            "reason": reason,
            "recommendation_count": len(recommendations),
        }

    async def _publish_event(self, event: dict[str, object]) -> None:
        publish = getattr(self.event_hub, "publish", None)
        if callable(publish):
            await publish(event)

    def _strategy_message(self, strategies: list[str]) -> str:
        if strategies == ["search", "related_chain"]:
            return "先从你刚刚的口味里搜一轮"
        if strategies == ["trending"]:
            return "顺手看看站内热榜里有没有你会吃的"
        if strategies == ["explore"]:
            return "再给你探一点你可能会意外喜欢的"
        return "正在继续给你补候选"

    def _is_initialized(self) -> bool:
        try:
            soul_layer = self.memory_manager.get_layer("soul")
        except Exception:
            return False
        data = getattr(soul_layer, "data", {})
        return isinstance(data, dict) and bool(data)

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime | None:
        if not value:
            return None
        with suppress(ValueError):
            return datetime.fromisoformat(value)
        return None

    @staticmethod
    def _int_state_value(state: dict[str, object], key: str) -> int:
        value = state.get(key, 0)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            with suppress(ValueError):
                return int(value)
        return 0

    def _is_due(self, value: str, *, hours: int) -> bool:
        if hours <= 0:
            return True
        last_run = self._parse_iso_datetime(value)
        if last_run is None:
            return True
        return self._now() - last_run >= timedelta(hours=hours)

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @staticmethod
    def _list_state_value(state: dict[str, object], key: str) -> list[str]:
        raw_value = state.get(key, [])
        if not isinstance(raw_value, list):
            return []
        return [str(item).strip() for item in raw_value if str(item).strip()]

    @staticmethod
    def _extract_topics(discovered: list[Any]) -> list[str]:
        topics: list[str] = []
        strategy_map = {
            "search": "相近兴趣",
            "related_chain": "相关推荐",
            "trending": "站内热榜",
            "explore": "跨圈探索",
        }
        for item in discovered:
            tags: Any = (
                item.get("tags", []) if isinstance(item, dict) else getattr(item, "tags", [])
            )
            if isinstance(tags, list):
                for tag in tags:
                    text = str(tag).strip()
                    if text:
                        topics.append(text)
            if isinstance(item, dict):
                source_strategy = str(item.get("source_strategy", "")).strip()
            else:
                source_strategy = str(getattr(item, "source_strategy", "")).strip()
            if source_strategy:
                topics.append(strategy_map.get(source_strategy, source_strategy))
        return topics

    @staticmethod
    def _dedupe_topics(topics: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for topic in topics:
            text = topic.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            ordered.append(text)
        return ordered
