"""Tests for the BackgroundTaskRegistry (v0.3.63+).

Covers the contract documented in ``src/openbiliclaw/runtime/task_registry.py``:

- ``track`` returns the spawned ``asyncio.Task`` and records it
- Completed tasks self-untrack via the ``add_done_callback`` hook
- ``cancel_all`` cancels every tracked task and reports the count
- A "stuck" task that ignores cancellation triggers a warning, returns within
  the grace budget, and stays tracked for later cleanup
- ``stats`` groups live tasks by name prefix

All tests are async — pytest's ``asyncio_mode = "auto"`` config applies.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry


async def test_track_returns_task_and_registers_it() -> None:
    registry = BackgroundTaskRegistry()

    async def _hold() -> None:
        await asyncio.sleep(10)

    task = registry.track("hold", _hold())
    try:
        assert isinstance(task, asyncio.Task)
        assert len(registry._tasks) == 1
        assert registry._tasks[task] == "hold"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_completed_task_self_untracks() -> None:
    registry = BackgroundTaskRegistry()

    async def _quick() -> int:
        return 7

    task = registry.track("quick", _quick())
    result = await task
    # ``add_done_callback`` is scheduled separately from the awaited task,
    # so allow one event-loop turn for the callback to fire.
    await asyncio.sleep(0)
    assert result == 7
    assert len(registry._tasks) == 0


async def test_cancel_all_cancels_every_task_and_returns_count() -> None:
    registry = BackgroundTaskRegistry()

    async def _hold() -> None:
        await asyncio.sleep(10)

    registry.track("a", _hold())
    registry.track("b", _hold())
    registry.track("c", _hold())

    cancelled = await registry.cancel_all()
    assert cancelled == 3
    assert len(registry._tasks) == 0


async def test_cancel_all_with_hung_task_is_bounded_and_remains_tracked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cancellation-swallowing task cannot hold hot reload forever."""
    registry = BackgroundTaskRegistry()
    started = asyncio.Event()
    stop = asyncio.Event()

    async def _stubborn() -> None:
        started.set()
        while not stop.is_set():
            try:
                await stop.wait()
            except asyncio.CancelledError:
                continue

    task = registry.track("stuck", _stubborn())
    await started.wait()

    try:
        with caplog.at_level(logging.WARNING, logger="openbiliclaw.runtime.task_registry"):
            cancelled = await asyncio.wait_for(registry.cancel_all(grace_seconds=0.01), timeout=0.2)
        assert cancelled == 1
        assert registry._tasks.get(task) == "stuck"
        assert not task.done()
        assert any("did not exit within" in record.message for record in caplog.records), (
            f"expected warning log, got {[r.message for r in caplog.records]}"
        )
    finally:
        stop.set()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(task, timeout=0.2)
        await asyncio.sleep(0)
        assert task not in registry._tasks


async def test_cancel_all_does_not_await_foreign_loop_task(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A task retained by an embedded host's prior loop cannot block reload."""
    registry = BackgroundTaskRegistry()

    class ForeignPendingTask:
        def done(self) -> bool:
            return False

        def get_loop(self) -> object:
            return object()

        def cancel(self) -> None:
            raise AssertionError("foreign-loop task must not be cancelled cross-loop")

    task = ForeignPendingTask()
    registry._tasks[task] = "foreign"  # type: ignore[index]

    with caplog.at_level(logging.WARNING, logger="openbiliclaw.runtime.task_registry"):
        cancelled = await asyncio.wait_for(registry.cancel_all(grace_seconds=0.01), timeout=0.2)

    assert cancelled == 1
    assert registry._tasks.get(task) == "foreign"  # type: ignore[arg-type]
    assert any("did not exit within" in record.message for record in caplog.records)
    registry._tasks.clear()


async def test_stats_groups_by_name_prefix() -> None:
    registry = BackgroundTaskRegistry()

    async def _hold() -> None:
        await asyncio.sleep(10)

    registry.track("refresh.manual", _hold())
    registry.track("refresh.precompute", _hold())
    registry.track("delight.scoring", _hold())
    registry.track("plain", _hold())

    try:
        stats = registry.stats()
        assert stats == {"refresh": 2, "delight": 1, "plain": 1}
    finally:
        await registry.cancel_all()


async def test_cancel_all_exclude_keeps_named_task() -> None:
    registry = BackgroundTaskRegistry()

    async def _hold() -> None:
        await asyncio.sleep(10)

    keep = registry.track("guided_init", _hold())
    registry.track("a", _hold())
    registry.track("b", _hold())

    try:
        cancelled = await registry.cancel_all(exclude=frozenset({"guided_init"}))
        assert cancelled == 2  # a + b, not guided_init
        assert not keep.done()  # excluded task left running
        assert registry._tasks.get(keep) == "guided_init"  # still tracked
    finally:
        await registry.cancel_all()
        with pytest.raises(asyncio.CancelledError):
            await keep


async def test_cancel_by_name_stops_only_that_task() -> None:
    registry = BackgroundTaskRegistry()

    async def _hold() -> None:
        await asyncio.sleep(10)

    registry.track("x", _hold())
    registry.track("x", _hold())
    other = registry.track("y", _hold())

    try:
        cancelled = await registry.cancel("x")
        assert cancelled == 2
        assert not other.done()
        assert list(registry._tasks.values()) == ["y"]
    finally:
        await registry.cancel_all()
        with pytest.raises(asyncio.CancelledError):
            await other
