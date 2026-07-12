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
        try:
            await future
        except asyncio.CancelledError:
            self._waiters = [entry for entry in self._waiters if entry[2] is not future]
            heapq.heapify(self._waiters)
            if future.done() and not future.cancelled():
                self._release_one()
            raise

    def release(self) -> None:
        if self._in_flight <= 0:
            raise RuntimeError("PrioritySemaphore released too many times")
        self._release_one()

    def _release_one(self) -> None:
        while self._waiters:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                future.set_result(None)
                return
        self._in_flight = max(0, self._in_flight - 1)

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


class LLMConcurrencyGate:
    """One true total provider bound plus a reserved background bound."""

    def __init__(self, total_concurrency: int = DEFAULT_TOTAL_LLM_CONCURRENCY) -> None:
        self.total_concurrency = coerce_total_concurrency(total_concurrency)
        self.background_concurrency = background_llm_concurrency(self.total_concurrency)
        self._total = PrioritySemaphore(self.total_concurrency)
        self._background = PrioritySemaphore(self.background_concurrency)
        self._warned_unknown_callers: set[str] = set()

    def classify(self, caller: str) -> LLMTrafficClass:
        tag = caller.strip()
        if tag in _INTERACTIVE_CALLERS:
            return LLMTrafficClass.INTERACTIVE
        if tag in _EXPRESSION_CALLERS:
            return LLMTrafficClass.REFILL_EXPRESSION
        if tag in _EVALUATION_CALLERS:
            return LLMTrafficClass.REFILL_EVALUATION
        if any(tag == prefix or tag.startswith(prefix + ".") for prefix in _SUPPLY_PREFIXES):
            return LLMTrafficClass.REFILL_SUPPLY
        if tag.startswith("sources.") and tag.endswith(".extract"):
            return LLMTrafficClass.MAINTENANCE
        if any(tag.startswith(prefix) for prefix in _KNOWN_MAINTENANCE_PREFIXES):
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
            LLMTrafficClass.REFILL_EVALUATION: 1,
            LLMTrafficClass.REFILL_SUPPLY: 2,
            LLMTrafficClass.MAINTENANCE: 3,
        }[traffic]

    @asynccontextmanager
    async def slot(self, *, caller: str, bypass_background: bool = False) -> AsyncIterator[None]:
        traffic = self.classify(caller)
        priority = self._priority(traffic)
        uses_background = traffic is not LLMTrafficClass.INTERACTIVE and not bypass_background
        if uses_background:
            async with self._background.slot(priority), self._total.slot(priority):
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
        }
