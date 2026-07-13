"""Runtime-wide admission control for all LLM provider traffic."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

DEFAULT_TOTAL_LLM_CONCURRENCY = 4


def coerce_total_concurrency(value: object) -> int:
    """Normalize a positive total, preserving every explicit positive value."""
    if isinstance(value, bool):
        return DEFAULT_TOTAL_LLM_CONCURRENCY
    if isinstance(value, int | float):
        normalized = int(value)
    elif isinstance(value, str):
        try:
            normalized = int(value.strip())
        except ValueError:
            return DEFAULT_TOTAL_LLM_CONCURRENCY
    else:
        return DEFAULT_TOTAL_LLM_CONCURRENCY
    return normalized if normalized >= 1 else DEFAULT_TOTAL_LLM_CONCURRENCY


def background_llm_concurrency(total: object) -> int:
    """Reserve one total slot for interactive work, degrading safely at one."""
    return max(1, coerce_total_concurrency(total) - 1)


class PrioritySemaphore:
    """Cancellation-safe semaphore serving lower priority numbers first."""

    def __init__(self, capacity: int = 1) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._in_flight = 0
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._counter = itertools.count()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def active(self) -> int:
        return self._in_flight

    @property
    def waiting(self) -> int:
        return sum(not future.done() for _, _, future in self._waiters)

    async def acquire(self, priority: int) -> None:
        if self._in_flight < self._capacity and not self._waiters:
            self._in_flight += 1
            return
        loop = asyncio.get_event_loop()
        future: asyncio.Future[None] = loop.create_future()
        heapq.heappush(self._waiters, (priority, next(self._counter), future))
        self._drain_waiters()
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            self._waiters = [entry for entry in self._waiters if entry[2] is not future]
            heapq.heapify(self._waiters)
            if future.done() and not future.cancelled():
                self.release()
            raise

    def release(self) -> None:
        if self._in_flight <= 0:
            raise RuntimeError("PrioritySemaphore released too many times")
        self._in_flight -= 1
        self._drain_waiters()

    def _drain_waiters(self) -> None:
        while self._in_flight < self._capacity and self._waiters:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                self._in_flight += 1
                future.set_result(None)

    def resize(self, capacity: int) -> None:
        """Change capacity without revoking active holders."""
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._drain_waiters()

    @asynccontextmanager
    async def slot(self, priority: int) -> AsyncIterator[None]:
        await self.acquire(priority)
        try:
            yield
        finally:
            self.release()


class LLMTrafficClass(StrEnum):
    INTERACTIVE = "interactive"
    REFILL_EXPRESSION = "refill.expression"
    REFILL_EVALUATION = "refill.evaluation"
    REFILL_SUPPLY = "refill.supply"
    MAINTENANCE = "maintenance"


class InventoryPriorityState(StrEnum):
    """Durable recommendation inventory state used for LLM admission."""

    HEALTHY = "healthy"
    REFILL = "refill"
    EMPTY = "empty"


class RefillAdmissionSemaphore:
    """Cancellation-safe, work-conserving admission for background traffic."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._active_total = 0
        self._active_refill = 0
        self._active_maintenance = 0
        self._waiting_refill = 0
        self._waiting_maintenance = 0
        self._inventory_state = InventoryPriorityState.HEALTHY
        self._waiters: list[tuple[int, int, LLMTrafficClass, asyncio.Future[None]]] = []
        self._counter = itertools.count()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def active(self) -> int:
        return self._active_total

    @property
    def waiting(self) -> int:
        return self._waiting_refill + self._waiting_maintenance

    @property
    def active_refill(self) -> int:
        return self._active_refill

    @property
    def active_maintenance(self) -> int:
        return self._active_maintenance

    @property
    def waiting_refill(self) -> int:
        return self._waiting_refill

    @property
    def waiting_maintenance(self) -> int:
        return self._waiting_maintenance

    @property
    def inventory_state(self) -> InventoryPriorityState:
        return self._inventory_state

    async def acquire(self, traffic: LLMTrafficClass, priority: int) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        heapq.heappush(
            self._waiters,
            (priority, next(self._counter), traffic, future),
        )
        self._increment_waiting(traffic)
        self._drain_waiters()
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                self.release(traffic)
            else:
                self._remove_waiter(future, traffic)
                self._drain_waiters()
            raise

    def release(self, traffic: LLMTrafficClass) -> None:
        if self._active_total <= 0:
            raise RuntimeError("RefillAdmissionSemaphore released too many times")
        self._active_total -= 1
        if traffic is LLMTrafficClass.MAINTENANCE:
            if self._active_maintenance <= 0:
                raise RuntimeError("maintenance admission released too many times")
            self._active_maintenance -= 1
        else:
            if self._active_refill <= 0:
                raise RuntimeError("refill admission released too many times")
            self._active_refill -= 1
        self._drain_waiters()

    def resize(self, capacity: int) -> None:
        """Change capacity without revoking active holders."""
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._drain_waiters()

    def update_inventory(self, state: InventoryPriorityState) -> None:
        """Apply a canonical inventory state and reconsider parked waiters."""
        self._inventory_state = state
        self._drain_waiters()

    @asynccontextmanager
    async def slot(self, traffic: LLMTrafficClass, priority: int) -> AsyncIterator[None]:
        await self.acquire(traffic, priority)
        try:
            yield
        finally:
            self.release(traffic)

    def _can_admit(self, traffic: LLMTrafficClass) -> bool:
        if self._active_total >= self.capacity:
            return False
        if traffic is not LLMTrafficClass.MAINTENANCE:
            return True
        if self._inventory_state is InventoryPriorityState.EMPTY:
            return False
        if self._waiting_refill > 0 and self._active_maintenance >= 1:  # noqa: SIM103
            return False
        return True

    def _drain_waiters(self) -> None:
        while self._active_total < self._capacity and self._waiters:
            admissible = [
                (priority, sequence, index)
                for index, (priority, sequence, traffic, future) in enumerate(self._waiters)
                if not future.done() and self._can_admit(traffic)
            ]
            admissible_index = min(admissible)[2] if admissible else None
            if admissible_index is None:
                return
            _, _, traffic, future = self._waiters.pop(admissible_index)
            heapq.heapify(self._waiters)
            self._decrement_waiting(traffic)
            self._active_total += 1
            if traffic is LLMTrafficClass.MAINTENANCE:
                self._active_maintenance += 1
            else:
                self._active_refill += 1
            future.set_result(None)

    def _remove_waiter(self, future: asyncio.Future[None], traffic: LLMTrafficClass) -> None:
        original_length = len(self._waiters)
        self._waiters = [entry for entry in self._waiters if entry[3] is not future]
        if len(self._waiters) != original_length:
            self._decrement_waiting(traffic)
            heapq.heapify(self._waiters)

    def _increment_waiting(self, traffic: LLMTrafficClass) -> None:
        if traffic is LLMTrafficClass.MAINTENANCE:
            self._waiting_maintenance += 1
        else:
            self._waiting_refill += 1

    def _decrement_waiting(self, traffic: LLMTrafficClass) -> None:
        if traffic is LLMTrafficClass.MAINTENANCE:
            self._waiting_maintenance -= 1
        else:
            self._waiting_refill -= 1


_INTERACTIVE_CALLERS = {
    "soul.dialogue",
    "soul.dialogue.tools",
    "soul.dialogue.tool_followup",
    "api.sentiment",
}
_EXPRESSION_CALLERS = {
    "recommendation.expression",
    "recommendation.write_expression",
}
_EVALUATION_CALLERS = {
    "discovery.evaluate_batch",
    "discovery.evaluate_single",
    "recommendation.evaluate_batch",
}
_SUPPLY_PREFIXES = (
    "discovery.douyin.keyword_gen",
    "discovery.keyword_inspiration",
    "discovery.keyword_planner",
    "discovery.search.queries",
    "discovery.x.keyword_gen",
    "runtime.bilibili_extension_search.queries",
    "sources.xhs.keyword_gen",
    "yt_search.generate_queries",
)
_KNOWN_MAINTENANCE_PREFIXES = (
    "discovery.",
    "eval.",
    "pool_purge.",
    "soul.",
    "sources.",
)
_MAINTENANCE_CALLERS = {"api.config_probe"}


class LLMConcurrencyGate:
    """One true total provider bound plus a reserved background bound."""

    def __init__(self, total_concurrency: int = DEFAULT_TOTAL_LLM_CONCURRENCY) -> None:
        self.total_concurrency = coerce_total_concurrency(total_concurrency)
        self.background_concurrency = background_llm_concurrency(self.total_concurrency)
        self._total = PrioritySemaphore(self.total_concurrency)
        self._background = RefillAdmissionSemaphore(self.background_concurrency)
        self._warned_unknown_callers: set[str] = set()

    @property
    def inventory_priority_state(self) -> InventoryPriorityState:
        """Return the current durable inventory classification."""
        return self._background.inventory_state

    def update_inventory(self, *, available: int, target: int) -> None:
        """Update refill admission from a canonical durable inventory snapshot."""
        normalized_available = max(0, int(available))
        normalized_target = max(0, int(target))
        if normalized_target <= 0 or normalized_available >= normalized_target:
            state = InventoryPriorityState.HEALTHY
        elif normalized_available == 0:
            state = InventoryPriorityState.EMPTY
        else:
            state = InventoryPriorityState.REFILL
        self._background.update_inventory(state)

    def reconfigure(self, total_concurrency: int) -> None:
        """Resize this runtime-owned gate in place for a hot reload."""
        total = coerce_total_concurrency(total_concurrency)
        background = background_llm_concurrency(total)
        self.total_concurrency = total
        self.background_concurrency = background
        self._total.resize(total)
        self._background.resize(background)

    def classify(self, caller: str) -> LLMTrafficClass:
        tag = caller.strip()
        if tag in _INTERACTIVE_CALLERS:
            return LLMTrafficClass.INTERACTIVE
        if tag in _EXPRESSION_CALLERS:
            return LLMTrafficClass.REFILL_EXPRESSION
        if tag in _EVALUATION_CALLERS:
            return LLMTrafficClass.REFILL_EVALUATION
        if any(tag == prefix or tag.startswith(prefix + ".") for prefix in _SUPPLY_PREFIXES):
            if self.inventory_priority_state is not InventoryPriorityState.HEALTHY:
                return LLMTrafficClass.REFILL_SUPPLY
            return LLMTrafficClass.MAINTENANCE
        if tag.startswith("sources.") and tag.endswith(".extract"):
            return LLMTrafficClass.MAINTENANCE
        if tag in _MAINTENANCE_CALLERS or any(
            tag.startswith(prefix) for prefix in _KNOWN_MAINTENANCE_PREFIXES
        ):
            return LLMTrafficClass.MAINTENANCE
        if tag not in self._warned_unknown_callers:
            self._warned_unknown_callers.add(tag)
            logger.warning("Unknown LLM caller %r; classifying as maintenance", tag)
        return LLMTrafficClass.MAINTENANCE

    @staticmethod
    def _priority(traffic: LLMTrafficClass) -> int:
        return {
            LLMTrafficClass.INTERACTIVE: 0,
            LLMTrafficClass.REFILL_EXPRESSION: 1,
            LLMTrafficClass.REFILL_EVALUATION: 2,
            LLMTrafficClass.REFILL_SUPPLY: 3,
            LLMTrafficClass.MAINTENANCE: 4,
        }[traffic]

    @asynccontextmanager
    async def slot(self, *, caller: str, bypass_background: bool = False) -> AsyncIterator[None]:
        traffic = self.classify(caller)
        priority = self._priority(traffic)
        uses_background = traffic is not LLMTrafficClass.INTERACTIVE and not bypass_background
        if uses_background:
            async with self._background.slot(traffic, priority), self._total.slot(priority):
                yield
            return
        async with self._total.slot(priority):
            yield

    def status_payload(self) -> dict[str, int | str | bool]:
        return {
            "llm_total_concurrency": self.total_concurrency,
            "llm_background_concurrency": self.background_concurrency,
            "llm_total_active": self._total.active,
            "llm_total_waiting": self._total.waiting,
            "llm_background_active": self._background.active,
            "llm_background_waiting": self._background.waiting,
            "llm_refill_active": self._background.active_refill,
            "llm_refill_waiting": self._background.waiting_refill,
            "llm_maintenance_active": self._background.active_maintenance,
            "llm_maintenance_waiting": self._background.waiting_maintenance,
            "llm_refill_priority_active": (
                self.inventory_priority_state is not InventoryPriorityState.HEALTHY
            ),
            "inventory_priority_state": self.inventory_priority_state.value,
        }
