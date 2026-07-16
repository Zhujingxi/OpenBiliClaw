"""Canonical in-process coordination for whole-file configuration writers."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


class ConfigWriteBusyError(RuntimeError):
    """A synchronous writer cannot join another task's active transaction."""

    def __init__(self) -> None:
        super().__init__("configuration write transaction is busy")


@dataclass
class ConfigWriteBoundary:
    """One path-keyed async transaction and bounded synchronous disk gate."""

    transaction_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    disk_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    owner_task: asyncio.Task[object] | None = field(default=None, repr=False)


_BOUNDARIES: dict[Path, ConfigWriteBoundary] = {}
_BOUNDARIES_LOCK = threading.Lock()


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def config_write_boundary(path: str | Path) -> ConfigWriteBoundary:
    """Return the process-wide coordination object for one resolved path."""
    resolved = _resolved(path)
    with _BOUNDARIES_LOCK:
        return _BOUNDARIES.setdefault(resolved, ConfigWriteBoundary())


def _current_task() -> asyncio.Task[object] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@asynccontextmanager
async def coordinated_config_write(path: str | Path) -> AsyncIterator[ConfigWriteBoundary]:
    """Own one complete async disk/runtime transaction for ``path``."""
    boundary = config_write_boundary(path)
    async with boundary.transaction_lock:
        owner = _current_task()
        if owner is None:  # pragma: no cover - async context invariant
            raise RuntimeError("configuration transaction requires an asyncio task")
        with boundary.disk_lock:
            boundary.owner_task = owner
        try:
            yield boundary
        finally:
            with boundary.disk_lock:
                boundary.owner_task = None


@contextmanager
def coordinated_config_disk_write(path: str | Path) -> Iterator[ConfigWriteBoundary]:
    """Serialize bounded sync I/O or fail fast behind another async owner.

    The lock is never held across an ``await``.  A synchronous caller running
    outside the active async owner fails rather than blocking an event loop and
    racing the owner's runtime swap or rollback.
    """
    boundary = config_write_boundary(path)
    with boundary.disk_lock:
        owner = boundary.owner_task
        if owner is not None and owner is not _current_task():
            raise ConfigWriteBusyError()
        yield boundary


__all__ = [
    "ConfigWriteBoundary",
    "ConfigWriteBusyError",
    "config_write_boundary",
    "coordinated_config_disk_write",
    "coordinated_config_write",
]
