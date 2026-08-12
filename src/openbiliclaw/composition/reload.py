"""Atomic application reference replacement and bounded old-graph drain."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openbiliclaw.core.config import AppSettings

    from .application import Application


class ApplicationBuilder(Protocol):
    def __call__(self, settings: AppSettings) -> Application: ...


class ApplicationReference:
    """One atomically swappable graph reference with request leases."""

    def __init__(self, application: Application) -> None:
        self._current = application
        self._lock = asyncio.Lock()
        self._leases: defaultdict[int, int] = defaultdict(int)
        self._idle: defaultdict[int, asyncio.Event] = defaultdict(asyncio.Event)

    @property
    def current(self) -> Application:
        return self._current

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Application]:
        async with self._lock:
            application = self._current
            identity = id(application)
            self._leases[identity] += 1
            self._idle[identity].clear()
        try:
            yield application
        finally:
            async with self._lock:
                self._leases[identity] -= 1
                if self._leases[identity] == 0:
                    self._idle[identity].set()

    async def swap(self, replacement: Application) -> Application:
        async with self._lock:
            old = self._current
            self._current = replacement
            return old

    async def drain(self, application: Application, timeout_seconds: float) -> bool:
        if timeout_seconds <= 0:
            raise ValueError("drain timeout must be positive")
        identity = id(application)
        async with self._lock:
            if self._leases[identity] == 0:
                self._drop(identity)
                return True
            event = self._idle[identity]
        try:
            async with asyncio.timeout(timeout_seconds):
                await event.wait()
        except TimeoutError:
            return False
        self._drop(identity)
        return True

    def _drop(self, identity: int) -> None:
        """Forget a fully drained graph so reloads cannot grow the maps."""
        self._leases.pop(identity, None)
        self._idle.pop(identity, None)


async def reload_application(
    reference: ApplicationReference,
    settings: AppSettings,
    *,
    builder: ApplicationBuilder,
    drain_timeout_seconds: float,
) -> bool:
    """Validate/build/start before swap; failed candidates never affect the active graph."""
    candidate = builder(settings)
    try:
        await candidate.start()
        if not await candidate.ready():
            raise RuntimeError("replacement application is not ready")
    except asyncio.CancelledError:
        await candidate.stop()
        raise
    except Exception:
        await candidate.stop()
        return False
    old = await reference.swap(candidate)
    drained = await reference.drain(old, drain_timeout_seconds)
    if not drained:
        # Deliberate: the old graph stops with leases still held; in-flight
        # callers keep their leased reference but no new work is admitted.
        pass
    await old.stop()
    return True
