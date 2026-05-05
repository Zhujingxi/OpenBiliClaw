"""Background task registry for cancel-on-hot-reload.

The runtime spawns many ``asyncio.create_task(...)`` calls for detached
fire-and-forget work — per-strategy precompute, prewarm helpers,
per-event triggers, manual refresh handles. When config changes at
runtime (``RuntimeContext.rebuild_from_config``), only the top-level
loop tasks were previously cancelled; detached tasks kept running with
references to the OLD runtime object, competing with the freshly built
runtime for SQLite writes and LLM tokens for many seconds after rebuild.

``BackgroundTaskRegistry`` is the single chokepoint every detached task
should flow through. ``cancel_all`` is awaited at the very top of
``rebuild_from_config`` so the new runtime starts from a clean slate.

Backward compatibility note: every caller that previously used
``asyncio.create_task`` directly continues to work unmodified — the
registry is wired in optionally, and code paths that don't have one
fall back to bare ``create_task`` exactly as before. This keeps the
existing test suite green without forcing every test fixture to inject
a registry.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class BackgroundTaskRegistry:
    """Tracks asyncio.create_task spawns so hot reload can cancel them.

    Every detached task that the runtime spawns (precompute, prewarm,
    per-event trigger, refresh-loop ticks) should pass through
    ``track`` instead of bare ``asyncio.create_task``. On
    ``cancel_all``, the registry cancels every still-running task and
    waits for them to settle.
    """

    def __init__(self) -> None:
        self._tasks: dict[asyncio.Task[Any], str] = {}

    def track(self, name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Wrap ``asyncio.create_task`` and remember the resulting task.

        Tasks self-untrack on completion via ``add_done_callback`` so the
        registry doesn't grow unbounded across a long-running daemon.
        """
        task = asyncio.create_task(coro, name=name)
        self._tasks[task] = name
        task.add_done_callback(lambda t: self._tasks.pop(t, None))
        return task

    async def cancel_all(self, *, grace_seconds: float = 1.5) -> int:
        """Cancel every tracked task and wait up to ``grace_seconds`` for cleanup.

        Returns the number of tasks that were tracked at the moment of
        the call (regardless of whether they finished cleanly or were
        force-abandoned at the grace timeout).
        """
        tasks = list(self._tasks.keys())
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=grace_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "%d background task(s) did not exit within %.1fs of cancel",
                    sum(1 for t in tasks if not t.done()),
                    grace_seconds,
                )
        # Self-untrack callbacks may not have fired for cancelled tasks
        # (especially when the grace timeout hit). Clear explicitly so
        # the registry is usable again immediately after rebuild.
        self._tasks.clear()
        return len(tasks)

    def stats(self) -> dict[str, int]:
        """Diagnostic: live task counts grouped by name prefix.

        The prefix is everything up to the first ``.`` in the task name
        (e.g. ``"precompute_pool_copy"`` → ``"precompute_pool_copy"``,
        ``"refresh.manual"`` → ``"refresh"``). Tasks created without
        a name fall under ``"unknown"``.
        """
        counts: dict[str, int] = {}
        for name in self._tasks.values():
            key = name.split(".", 1)[0] if name else "unknown"
            counts[key] = counts.get(key, 0) + 1
        return counts
