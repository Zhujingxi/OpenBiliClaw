"""Execution-lane hot-reload drain and timeout regressions."""

from __future__ import annotations

import asyncio

import pytest

from openbiliclaw.runtime.feedback_scheduler import EventProcessingScheduler


@pytest.mark.asyncio
async def test_pause_and_drain_never_cancels_owner_between_write_and_checkpoint() -> None:
    profile_written = asyncio.Event()
    allow_checkpoint = asyncio.Event()
    checkpoint_saved = asyncio.Event()
    cancelled = False

    class Engine:
        async def process_profile_events_if_needed(self) -> None:
            nonlocal cancelled
            profile_written.set()
            try:
                await allow_checkpoint.wait()
                checkpoint_saved.set()
            except asyncio.CancelledError:
                cancelled = True
                raise

        async def process_feedback_batch_if_needed(self) -> None:
            return None

    scheduler = EventProcessingScheduler(soul_engine=Engine(), debounce_seconds=0)
    scheduler.schedule()
    await asyncio.wait_for(profile_written.wait(), timeout=1)

    draining = asyncio.create_task(scheduler.pause_and_drain(timeout=1))
    await asyncio.sleep(0)
    assert draining.done() is False
    assert cancelled is False

    allow_checkpoint.set()
    await asyncio.wait_for(draining, timeout=1)

    assert checkpoint_saved.is_set()
    assert cancelled is False
    assert scheduler.status_payload()["event_lane_paused"] is True
    await scheduler.close()


@pytest.mark.asyncio
async def test_pause_timeout_resumes_old_lane_without_cancelling_owner() -> None:
    owner_started = asyncio.Event()
    release_owner = asyncio.Event()
    owner_finished = asyncio.Event()
    calls = 0
    cancelled = False

    class Engine:
        async def process_profile_events_if_needed(self) -> None:
            nonlocal calls, cancelled
            calls += 1
            if calls > 1:
                return
            owner_started.set()
            try:
                await release_owner.wait()
                owner_finished.set()
            except asyncio.CancelledError:
                cancelled = True
                raise

        async def process_feedback_batch_if_needed(self) -> None:
            return None

    scheduler = EventProcessingScheduler(
        soul_engine=Engine(),
        debounce_seconds=0,
        scan_interval_seconds=60,
    )
    scheduler.schedule()
    await asyncio.wait_for(owner_started.wait(), timeout=1)

    with pytest.raises(TimeoutError):
        await scheduler.pause_and_drain(timeout=0.02)

    assert cancelled is False
    assert scheduler.status_payload()["event_lane_paused"] is False
    release_owner.set()
    await asyncio.wait_for(owner_finished.wait(), timeout=1)
    await asyncio.wait_for(scheduler.drain(), timeout=1)
    assert calls >= 1
    await scheduler.close()
