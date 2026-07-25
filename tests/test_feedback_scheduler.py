from __future__ import annotations

import asyncio

import pytest

from openbiliclaw.runtime.feedback_scheduler import FeedbackBatchScheduler


class FakeSoulEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def process_feedback_batch_if_needed(self) -> dict[str, object]:
        self.calls += 1
        return {"triggered": True}


@pytest.mark.asyncio
async def test_feedback_batch_scheduler_coalesces_burst() -> None:
    soul = FakeSoulEngine()
    scheduler = FeedbackBatchScheduler(soul, debounce_seconds=0)

    for _ in range(5):
        scheduler.schedule()
    await scheduler.drain()

    assert soul.calls == 1


@pytest.mark.asyncio
async def test_feedback_batch_scheduler_runs_again_when_dirty_during_processing() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowSoulEngine(FakeSoulEngine):
        async def process_feedback_batch_if_needed(self) -> dict[str, object]:
            self.calls += 1
            if self.calls == 1:
                started.set()
                await release.wait()
            return {"triggered": True}

    soul = SlowSoulEngine()
    scheduler = FeedbackBatchScheduler(soul, debounce_seconds=0)

    scheduler.schedule()
    await started.wait()
    scheduler.schedule()
    release.set()
    await scheduler.drain()

    assert soul.calls == 2


@pytest.mark.asyncio
async def test_feedback_batch_scheduler_logs_info_on_no_provider(caplog) -> None:
    import logging

    from openbiliclaw.llm.base import LLMFallbackError

    class NoProviderSoulEngine(FakeSoulEngine):
        async def process_feedback_batch_if_needed(self) -> dict[str, object]:
            self.calls += 1
            raise LLMFallbackError("No provider was available to process the request.")

    soul = NoProviderSoulEngine()
    scheduler = FeedbackBatchScheduler(soul, debounce_seconds=0)

    caplog.set_level(logging.INFO)
    scheduler.schedule()
    await scheduler.drain()

    assert soul.calls == 1
    assert "no chat LLM provider configured yet" in caplog.text
    assert "post-feedback batch processing failed" not in caplog.text
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.asyncio
async def test_feedback_batch_scheduler_logs_warning_on_rate_limit(caplog) -> None:
    import logging

    from openbiliclaw.llm.base import LLMRateLimitError

    class RateLimitedSoulEngine(FakeSoulEngine):
        async def process_feedback_batch_if_needed(self) -> dict[str, object]:
            self.calls += 1
            raise LLMRateLimitError("429 rate limit exceeded")

    soul = RateLimitedSoulEngine()
    scheduler = FeedbackBatchScheduler(soul, debounce_seconds=0)

    caplog.set_level(logging.INFO)
    scheduler.schedule()
    await scheduler.drain()

    assert soul.calls == 1
    assert "rate-limited/cooling down" in caplog.text
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)
