from __future__ import annotations

import asyncio
import logging

import pytest

from openbiliclaw.llm.concurrency import (
    LLMConcurrencyGate,
    LLMTrafficClass,
    background_llm_concurrency,
)


async def _wait_until(predicate: object) -> None:
    for _ in range(100):
        if callable(predicate) and predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_background_concurrency_reserves_one_total_slot() -> None:
    assert background_llm_concurrency(4) == 3
    assert background_llm_concurrency(3) == 2
    assert background_llm_concurrency(1) == 1
    assert background_llm_concurrency("invalid") == 3


async def test_three_background_calls_leave_default_interactive_slot() -> None:
    gate = LLMConcurrencyGate(total_concurrency=4)
    release = asyncio.Event()
    entered = asyncio.Event()

    async def background() -> None:
        async with gate.slot(caller="soul.preference"):
            await release.wait()

    tasks = [asyncio.create_task(background()) for _ in range(4)]
    await _wait_until(lambda: gate.status_payload()["llm_background_active"] == 3)

    async def interactive() -> None:
        async with gate.slot(caller="soul.dialogue"):
            entered.set()
            await release.wait()

    interactive_task = asyncio.create_task(interactive())
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert gate.status_payload()["llm_total_active"] == 4
    release.set()
    await asyncio.gather(*tasks, interactive_task)


async def test_total_one_degrades_without_deadlock() -> None:
    gate = LLMConcurrencyGate(total_concurrency=1)
    async with gate.slot(caller="soul.preference"):
        assert gate.status_payload()["llm_total_active"] == 1
    async with asyncio.timeout(1):
        async with gate.slot(caller="soul.dialogue"):
            pass
    assert gate.status_payload()["llm_total_active"] == 0


@pytest.mark.parametrize("queued_caller", ["soul.dialogue", "soul.preference"])
async def test_cancelled_waiter_does_not_leak_capacity(queued_caller: str) -> None:
    gate = LLMConcurrencyGate(total_concurrency=1)
    release = asyncio.Event()

    async def holder() -> None:
        async with gate.slot(caller="soul.preference"):
            await release.wait()

    holder_task = asyncio.create_task(holder())
    await _wait_until(lambda: gate.status_payload()["llm_total_active"] == 1)
    waiter = asyncio.create_task(_acquire_once(gate, queued_caller))
    waiting_key = (
        "llm_total_waiting" if queued_caller == "soul.dialogue" else "llm_background_waiting"
    )
    await _wait_until(lambda: gate.status_payload()[waiting_key] == 1)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await holder_task
    await asyncio.wait_for(_acquire_once(gate, "soul.dialogue"), timeout=1)
    assert gate.status_payload()["llm_total_active"] == 0
    assert gate.status_payload()["llm_background_active"] == 0


async def _acquire_once(gate: LLMConcurrencyGate, caller: str) -> None:
    async with gate.slot(caller=caller):
        pass


@pytest.mark.parametrize(
    "caller",
    [
        "discovery.douyin.keyword_gen",
        "discovery.evaluate_batch",
        "discovery.evaluate_single",
        "discovery.explore.queries",
        "discovery.keyword_inspiration",
        "discovery.keyword_planner",
        "discovery.search.queries",
        "discovery.x.keyword_gen",
        "eval.query_quality",
        "eval.relevance",
        "eval.scenario_gen",
        "eval.specificity",
        "pool_purge.llm_agent",
        "recommendation.evaluate_batch",
        "recommendation.expression",
        "recommendation.write_expression",
        "runtime.bilibili_extension_search.queries",
        "soul.avoidance_speculate",
        "soul.awareness",
        "soul.category_migration",
        "soul.consolidation",
        "soul.core_update",
        "soul.dialogue_insight",
        "soul.insight",
        "soul.preference",
        "soul.preference.chunk",
        "soul.profile_build",
        "soul.role_update",
        "soul.speculate",
        "soul.values_update",
        "sources.xhs.keyword_gen",
        "sources.zhihu.extract",
        "yt_search.generate_queries",
    ],
)
def test_current_background_callers_are_classified(caller: str) -> None:
    assert LLMConcurrencyGate(4).classify(caller) is not LLMTrafficClass.INTERACTIVE


@pytest.mark.parametrize(
    "caller",
    ["soul.dialogue", "soul.dialogue.tools", "soul.dialogue.tool_followup", "api.sentiment"],
)
def test_confirmed_interactive_callers(caller: str) -> None:
    assert LLMConcurrencyGate(4).classify(caller) is LLMTrafficClass.INTERACTIVE


async def test_unknown_caller_warns_once_and_remains_background_limited(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = LLMConcurrencyGate(2)
    with caplog.at_level(logging.WARNING):
        await _acquire_once(gate, "new.unclassified")
        await _acquire_once(gate, "new.unclassified")
    assert sum("new.unclassified" in record.message for record in caplog.records) == 1


async def test_bypass_background_still_obeys_total_gate() -> None:
    gate = LLMConcurrencyGate(1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with gate.slot(caller="soul.preference", bypass_background=True):
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    waiter = asyncio.create_task(
        gate.slot(caller="soul.dialogue", bypass_background=True).__aenter__()
    )
    await asyncio.sleep(0)
    assert not waiter.done()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await task
