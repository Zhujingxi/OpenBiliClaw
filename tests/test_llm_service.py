"""Tests for the shared LLM service facade."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.service import (
    LLMProviderExecutionError,
    LLMResponseContentError,
    LLMService,
    PrioritySemaphore,
)
from openbiliclaw.memory.manager import MemoryManager

if TYPE_CHECKING:
    from pathlib import Path


class FakeRegistry:
    """Minimal fake registry for service tests."""

    def __init__(self, response: LLMResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[list[dict[str, str]]] = []
        self.json_modes: list[bool] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        self.json_modes.append(json_mode)
        if self.error is not None:
            raise self.error
        return self.response or LLMResponse(content="", provider="openai")


class FakeMemoryManager:
    def __init__(self, core_prompt: str) -> None:
        self.core_prompt = core_prompt

    def render_core_memory_prompt(self) -> str:
        return self.core_prompt


@pytest.mark.asyncio
async def test_llm_service_calls_registry_with_memory_context(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.get_layer("soul").update("personality_portrait", "喜欢深度叙事和结构化表达")
    registry = FakeRegistry(LLMResponse(content="当然，我们继续聊。", provider="openai"))
    service = LLMService(registry=registry, memory=memory)

    response = await service.complete_socratic_dialogue(
        user_message="我最近特别喜欢看长视频。",
        history=[{"role": "user", "content": "我喜欢能讲透的内容"}],
    )

    assert response.content == "当然，我们继续聊。"
    assert len(registry.calls) == 1
    assert registry.calls[0][0]["role"] == "system"
    assert "结构化表达" in registry.calls[0][0]["content"]
    assert "老B友" in registry.calls[0][0]["content"]


@pytest.mark.asyncio
async def test_llm_service_injects_empty_memory_placeholder(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(LLMResponse(content="我们可以慢慢聊。", provider="openai"))
    service = LLMService(registry=registry, memory=memory)

    await service.complete_socratic_dialogue(
        user_message="我最近想看点新东西。",
        history=[],
    )

    assert "尚未建立完整画像" in registry.calls[0][0]["content"]


@pytest.mark.asyncio
async def test_llm_service_raises_on_empty_response_content(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(LLMResponse(content="", provider="openai"))
    service = LLMService(registry=registry, memory=memory)

    with pytest.raises(LLMResponseContentError):
        await service.complete_socratic_dialogue(
            user_message="我想聊聊为什么我总在熬夜看视频。",
            history=[],
        )


@pytest.mark.asyncio
async def test_llm_service_wraps_provider_failures(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    registry = FakeRegistry(error=LLMProviderError("provider down"))
    service = LLMService(registry=registry, memory=memory)

    with pytest.raises(LLMProviderExecutionError):
        await service.complete_socratic_dialogue(
            user_message="我最近总在重复看同一类视频。",
            history=[],
        )


@pytest.mark.asyncio
async def test_complete_with_core_memory_injects_core_memory() -> None:
    registry = FakeRegistry(LLMResponse(content="ok", provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_with_core_memory(
        system_instruction="你是内容评估助手。",
        user_input="请评估这个视频。",
    )

    assert "## 用户画像" in registry.calls[0][0]["content"]
    assert "你是内容评估助手。" in registry.calls[0][0]["content"]
    assert registry.calls[0][1]["content"] == "请评估这个视频。"


@pytest.mark.asyncio
async def test_complete_structured_task_enables_json_mode() -> None:
    registry = FakeRegistry(LLMResponse(content='{"ok": true}', provider="openai"))
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    service = LLMService(registry=registry, memory=memory)  # type: ignore[arg-type]

    await service.complete_structured_task(
        system_instruction="输出 JSON。",
        user_input="请返回结构化结果。",
    )

    assert registry.calls
    assert registry.json_modes == [True]


def test_resolve_priority_longest_prefix_wins() -> None:
    """write_expression beats the catch-all default; soul-level prefix matches."""
    assert LLMService._resolve_priority("recommendation.write_expression") == 1
    assert LLMService._resolve_priority("discovery.evaluate_batch") == 1
    assert LLMService._resolve_priority("recommendation.delight_score") == 2
    assert LLMService._resolve_priority("soul.preference") == 2
    assert LLMService._resolve_priority("xhs.classify") == 2
    assert LLMService._resolve_priority("unrelated.tag") == LLMService._DEFAULT_PRIORITY
    assert LLMService._resolve_priority("") == LLMService._DEFAULT_PRIORITY


@pytest.mark.asyncio
async def test_priority_semaphore_orders_waiters_by_priority() -> None:
    """When multiple coroutines queue while the slot is held, lower-number priorities run first."""
    sem = PrioritySemaphore(capacity=1)
    log: list[str] = []
    blocker_release = asyncio.Event()

    async def blocker() -> None:
        async with sem.slot(priority=1):
            log.append("blocker.start")
            await blocker_release.wait()
            log.append("blocker.end")

    async def worker(name: str, priority: int) -> None:
        async with sem.slot(priority=priority):
            log.append(name)

    blocker_task = asyncio.create_task(blocker())
    # Give the blocker time to acquire the slot before the contenders queue up.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    low = asyncio.create_task(worker("low", priority=3))
    medium = asyncio.create_task(worker("medium", priority=2))
    high = asyncio.create_task(worker("high", priority=1))
    # Let all three workers reach the queue.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    blocker_release.set()
    await asyncio.gather(blocker_task, low, medium, high)

    assert log[0] == "blocker.start"
    assert log[1] == "blocker.end"
    # Highest priority (lowest number) should be served first after the blocker frees the slot.
    assert log[2:] == ["high", "medium", "low"]


@pytest.mark.asyncio
async def test_complete_with_core_memory_serialises_calls_through_priority_gate() -> None:
    """write_expression should hold the slot exclusively and waiters serialise behind it."""
    memory = FakeMemoryManager(core_prompt="## 用户画像\nportrait")
    in_flight = 0
    peak = 0
    release = asyncio.Event()

    class TrackingRegistry:
        async def complete(
            self,
            messages: list[dict[str, str]],
            *,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            json_mode: bool = False,
            reasoning_effort: str | None = None,
        ) -> LLMResponse:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await release.wait()
            finally:
                in_flight -= 1
            return LLMResponse(content="ok", provider="openai")

    service = LLMService(registry=TrackingRegistry(), memory=memory)  # type: ignore[arg-type]

    first = asyncio.create_task(
        service.complete_with_core_memory(
            system_instruction="A",
            user_input="A",
            caller="recommendation.write_expression",
        )
    )
    # Wait until the first call has acquired the slot.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    second = asyncio.create_task(
        service.complete_with_core_memory(
            system_instruction="B",
            user_input="B",
            caller="recommendation.delight_score",
        )
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Second call must NOT have started yet — the gate is held.
    assert in_flight == 1

    release.set()
    await asyncio.gather(first, second)
    # capacity=1 means only one provider call ever ran at a time.
    assert peak == 1
