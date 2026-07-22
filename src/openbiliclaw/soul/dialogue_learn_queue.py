"""Single-worker serial queue for dialogue-learning tasks (Phase 1).

``learn_from_dialogue`` reads-merges-writes shared preference / profile state.
Firing it via ``asyncio.create_task`` per turn let adjacent turns interleave
their read/merge/write (a concurrency bug). This queue serializes them: every
turn is appended and a single worker consumes them one at a time.

Lifecycle ownership (spec §Design invariants 7): the worker is **self-owned** —
it is NOT registered in the runtime's ``BackgroundTaskRegistry`` / ``cancel_all``
management. A config hot-reload's ``cancel_all`` therefore never kills it out
from under an in-flight learn. Instead the queue exposes explicit
``pause`` / ``resume`` / ``pause_and_drain`` / ``shutdown`` so the reload path
can drain the old queue *before* ``cancel_all`` and, on a construction failure,
``resume`` the old queue (it was never cancelled).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

logger = logging.getLogger(__name__)

# Depth beyond which we warn: the worker (an LLM round per turn) is falling
# behind the user's typing. Not a hard cap — dropping a user's correction is
# worse than a slow queue — but a signal that the provider is slow.
_QUEUE_DEPTH_WARN = 10


class DialogueLearnQueue:
    """Serializes ``learn_from_dialogue`` invocations through one worker.

    ``handler`` is an async callable invoked with the payload's keyword
    arguments (``user_message`` / ``assistant_reply`` / ``session`` /
    ``scope`` / ``turn_id`` / ``anchor_ref`` / ``anchor_generation``).
    """

    def __init__(
        self,
        handler: Callable[..., Awaitable[Any]],
        *,
        name: str = "dialogue_learn_worker",
        anchor_provider: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        self._handler = handler
        self._name = name
        self._anchor_provider = anchor_provider
        self._queue: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._accepting = True
        # ``_resume`` set == worker may consume; cleared == worker blocks after
        # dequeuing the next item (used by ``pause`` for reload rollback).
        self._resume = asyncio.Event()
        self._resume.set()
        self._closed = False

    # -- Lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Start the single worker task (idempotent, loop-tolerant).

        If called outside a running event loop (e.g. synchronous startup),
        the worker is deferred and started on the first ``submit`` — which
        always runs inside the loop.
        """
        if self._closed:
            raise RuntimeError("DialogueLearnQueue is closed")
        if self._worker is None or self._worker.done():
            # Probe for a running loop BEFORE building the coroutine: calling
            # ``self._run()`` first and letting ``create_task`` raise leaves an
            # orphaned never-awaited coroutine ("coroutine ... was never
            # awaited" RuntimeWarning at synchronous startup).
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # No running loop yet; the worker will start on first submit.
                self._worker = None
                return
            self._worker = asyncio.create_task(self._run(), name=self._name)

    @property
    def worker_alive(self) -> bool:
        return self._worker is not None and not self._worker.done()

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    def pause(self) -> None:
        """Stop accepting new turns and block the worker after its current item.

        Used on the hot-reload rollback path: the worker is preserved (not
        cancelled), so ``resume`` restores consumption of whatever is queued.
        """
        self._accepting = False
        self._resume.clear()

    def resume(self) -> None:
        """Re-enable accepting turns and unblock the worker."""
        if self._closed:
            return
        self._accepting = True
        self._resume.set()

    async def pause_and_drain(self, *, timeout: float | None = None) -> None:
        """Stop accepting new turns, then let the worker finish the backlog.

        Executed BEFORE ``cancel_all`` on hot-reload so no learn is lost. The
        worker keeps consuming (``_resume`` stays set) until the queue empties.
        """
        self._accepting = False
        self._resume.set()
        await self._join(timeout=timeout)

    async def shutdown(self, *, timeout: float | None = None) -> None:
        """Drain remaining turns then stop the worker (shutdown / stop-old)."""
        self._closed = True
        self._accepting = False
        self._resume.set()
        try:
            await self._join(timeout=timeout)
        finally:
            worker = self._worker
            if worker is not None:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await worker
                self._worker = None

    async def _join(self, *, timeout: float | None) -> None:
        if timeout is None:
            await self._queue.join()
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout)
        except TimeoutError:
            logger.warning(
                "DialogueLearnQueue drain timed out after %.1fs (depth=%d)",
                timeout,
                self._queue.qsize(),
            )
            raise

    # -- Submission -----------------------------------------------------------

    async def submit(self, payload: Mapping[str, Any]) -> bool:
        """Append a learn payload. Returns False (and logs) if not accepting."""
        if self._closed or not self._accepting:
            logger.warning(
                "DialogueLearnQueue not accepting; dropped turn (scope=%s, closed=%s)",
                payload.get("scope"),
                self._closed,
            )
            return False
        # Lazily start the worker if ``start`` was called before a loop existed.
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name=self._name)
        queued_payload = dict(payload)
        anchor_snapshot: Mapping[str, object] = {}
        if self._anchor_provider is not None:
            try:
                anchor_snapshot = self._anchor_provider()
            except Exception:
                logger.warning("Failed to capture dialogue anchor snapshot", exc_info=True)
        queued_payload["anchor_ref"] = str(anchor_snapshot.get("anchor_ref", "") or "")
        try:
            raw_generation = anchor_snapshot.get("anchor_generation", 0)
            if not isinstance(raw_generation, (int, str)):
                raise TypeError
            queued_payload["anchor_generation"] = max(
                0,
                int(raw_generation),
            )
        except (TypeError, ValueError):
            logger.warning("Invalid dialogue anchor generation in snapshot; using zero")
            queued_payload["anchor_generation"] = 0
        await self._queue.put(queued_payload)
        depth = self._queue.qsize()
        if depth >= _QUEUE_DEPTH_WARN:
            logger.warning(
                "DialogueLearnQueue depth=%d — learning is falling behind (slow LLM?).",
                depth,
            )
        return True

    # -- Worker ---------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            payload = await self._queue.get()
            try:
                await self._resume.wait()
                await self._handler(**dict(payload))
            except Exception:
                logger.exception("Dialogue learn task failed (scope=%s)", payload.get("scope"))
            finally:
                self._queue.task_done()
