"""Single-owner scheduling for durable generic and feedback event cursors."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openbiliclaw.llm.base import classify_llm_unavailability

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass
class EventProcessingScheduler:
    """Coalesce wakes while periodically recovering committed event rows."""

    soul_engine: Any = None
    debounce_seconds: float = 5.0
    soul_engine_resolver: Callable[[], Any] | None = None
    scan_interval_seconds: float = 5.0
    _dirty: bool = field(default=False, init=False)
    _closed: bool = field(default=False, init=False)
    _paused: bool = field(default=False, init=False)
    _active: bool = field(default=False, init=False)
    _last_error: str = field(default="", init=False)
    _processed: int = field(default=0, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _periodic_task: asyncio.Task[None] | None = field(default=None, init=False)
    _owner_task: asyncio.Task[object] | None = field(default=None, init=False)
    _process_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def schedule(self) -> None:
        """Wake the durable owner; the wake itself carries no facts."""
        if self._closed:
            return
        self._dirty = True
        if self._paused:
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="event-processing")

    def start_background_recovery(self) -> asyncio.Task[None] | None:
        """Admit one detached recovery pass and expose its owned task.

        FastAPI startup uses this after publishing the local owner fences.  The
        returned task is deliberately *not* awaited by the lifespan handler:
        buffer consumption can enter a provider call, while the committed
        event rows and cursor checkpoints make later retry authoritative.  The
        scheduler still owns cancellation/drain through :meth:`close` and
        :meth:`pause_and_drain`.
        """
        self.schedule()
        return self._task

    def start_periodic(self) -> None:
        """Start the source/account-sync safety-net scanner."""
        if self._closed or self._paused:
            return
        if self._periodic_task is None or self._periodic_task.done():
            self._periodic_task = asyncio.create_task(
                self._periodic_loop(),
                name="event-processing-periodic",
            )

    async def recover(self) -> None:
        """Synchronously recover owners before any external pipeline tick starts."""
        if self._closed or self._paused:
            return
        if self._task is not None and not self._task.done():
            await self.drain()
            return
        await self._process_once()

    async def pause_and_drain(self, *, timeout: float = 1500.0) -> None:
        """Quiesce timers and let an active owner finish exactly once.

        A layer write can occur before the final pipeline-state save. Cancelling
        in that window would replay already-applied profile side effects on the
        replacement engine, so hot reload must drain, not cancel. A timeout
        resumes the old lane and aborts the caller's handoff.
        """
        if self._closed or self._paused:
            return
        self._paused = True
        self._dirty = True
        current = asyncio.current_task()
        owner = self._owner_task
        cancellable: set[asyncio.Task[Any]] = set()
        for candidate in (self._periodic_task, self._task):
            if (
                candidate is not None
                and candidate is not current
                and candidate is not owner
                and not candidate.done()
            ):
                cancellable.add(candidate)
        for task in cancellable:
            task.cancel()
        for task in cancellable:
            with suppress(asyncio.CancelledError):
                await task
        self._periodic_task = None
        if self._task in cancellable or (self._task is not None and self._task.done()):
            self._task = None

        if owner is None or owner is current or owner.done():
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(owner),
                timeout=max(0.01, float(timeout)),
            )
        except BaseException:
            # Do not publish a new runtime while the old owner can still write.
            # Restore its scheduling surface and let the rebuild fail closed.
            self._paused = False
            self.start_periodic()
            self.schedule()
            raise
        finally:
            if self._task is not None and self._task.done():
                self._task = None

    async def pause(self) -> None:
        """Compatibility alias for the hot-reload drain contract."""
        await self.pause_and_drain()

    async def resume(self, *, recover: bool = True) -> None:
        """Resume against the resolver's current runtime, optionally recovering now."""
        if self._closed:
            return
        self._paused = False
        if recover:
            await self.recover()
        self.start_periodic()

    async def drain(self) -> None:
        """Wait for currently requested passes without stopping periodic scan."""
        while True:
            task = self._task
            if task is None:
                return
            await task
            if not self._dirty and not self._active:
                return

    async def close(self) -> None:
        """Shutdown may cancel active work; durable state permits later retry."""
        if self._closed:
            return
        self._closed = True
        self._paused = True
        current = asyncio.current_task()
        tasks: set[asyncio.Task[Any]] = set()
        for candidate in (self._periodic_task, self._task, self._owner_task):
            if candidate is not None and candidate is not current and not candidate.done():
                tasks.add(candidate)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._periodic_task = None
        self._task = None

    def status_payload(self) -> dict[str, object]:
        """Return non-sensitive lane diagnostics for runtime status."""
        return {
            "event_lane_depth": int(self._dirty),
            "event_lane_active": self._active,
            "event_lane_paused": self._paused,
            "event_lane_last_error": self._last_error,
            "event_lane_processed": self._processed,
        }

    def _current_soul_engine(self) -> Any:
        resolver = self.soul_engine_resolver
        return resolver() if resolver is not None else self.soul_engine

    async def _periodic_loop(self) -> None:
        while not self._closed and not self._paused:
            await asyncio.sleep(max(0.1, float(self.scan_interval_seconds)))
            if not self._paused:
                self.schedule()

    async def _run(self) -> None:
        while not self._closed and not self._paused:
            delay = max(0.0, float(self.debounce_seconds))
            if delay > 0:
                await asyncio.sleep(delay)
            self._dirty = False
            await self._process_once()
            if not self._dirty:
                return

    async def _process_once(self) -> None:
        async with self._process_lock:
            if self._closed or self._paused:
                return
            engine = self._current_soul_engine()
            if engine is None:
                return
            current = asyncio.current_task()
            self._owner_task = current
            self._active = True
            try:
                process_profile = getattr(engine, "process_profile_events_if_needed", None)
                if callable(process_profile):
                    await process_profile()
                process_feedback = getattr(engine, "process_feedback_batch_if_needed", None)
                if callable(process_feedback):
                    await process_feedback()
                self._last_error = ""
                self._processed += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = type(exc).__name__
                kind = classify_llm_unavailability(exc)
                if kind == "no_provider":
                    logger.info("event processing deferred: no chat LLM provider configured yet")
                elif kind == "model_not_found":
                    logger.warning("event processing deferred: configured chat model was not found")
                elif kind == "rate_limited":
                    logger.warning(
                        "event processing deferred: LLM provider rate-limited/cooling down"
                    )
                else:
                    logger.exception("durable event processing failed")
            finally:
                if self._owner_task is current:
                    self._owner_task = None
                self._active = False


# Compatibility import/injection surface retained for existing integrations.
FeedbackBatchScheduler = EventProcessingScheduler
