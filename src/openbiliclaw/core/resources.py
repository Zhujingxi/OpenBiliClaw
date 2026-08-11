"""Named concurrency budgets for all supervised runtime work."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class ResourceBudget:
    """A named, bounded semaphore exposed as an async context manager."""

    def __init__(self, name: str, limit: int) -> None:
        if not name:
            raise ValueError("resource name must not be empty")
        if limit < 1:
            raise ValueError("resource limit must be positive")
        self._name = name
        self._limit = limit
        self._active = 0
        self._semaphore = asyncio.Semaphore(limit)

    @property
    def name(self) -> str:
        return self._name

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """Wait for one slot and always release it on exit or cancellation."""
        await self._semaphore.acquire()
        self._active += 1
        try:
            yield
        finally:
            self._active -= 1
            self._semaphore.release()
