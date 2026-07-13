"""Tests for Socratic dialogue integration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.service import LLMResponseContentError, ModuleOverride
from openbiliclaw.soul.dialogue import DialogueTurn, SocraticDialogue


class FakeSoulEngine:
    """Minimal soul engine stub for dialogue tests."""

    def __init__(self) -> None:
        self.learn_calls: list[str] = []

    async def learn_from_dialogue(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        session: str,
    ) -> None:
        self.learn_calls.append(f"{session}:{user_message}->{assistant_reply}")


class FakeService:
    """Minimal shared service stub."""

    def __init__(self, *, response: str | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def complete_socratic_dialogue(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]],
        caller: str = "",
    ) -> LLMResponse:
        self.calls.append({"user_message": user_message, "history": history})
        if self.error is not None:
            raise self.error
        return LLMResponse(content=self.response or "", provider="openai")


@pytest.mark.asyncio
async def test_dialogue_respond_appends_user_and_agent_turns() -> None:
    service = FakeService(response="我猜你喜欢的是那种能慢慢展开逻辑的讲述方式。")
    soul_engine = FakeSoulEngine()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=service,
    )

    reply = await dialogue.respond("我最近很喜欢看讲得很透的纪录片。")
    await asyncio.sleep(0)  # let background learn task run

    assert "讲述方式" in reply
    assert len(dialogue.history) == 2
    assert dialogue.history[0].role == "user"
    assert dialogue.history[1].role == "agent"
    assert service.calls[0]["user_message"] == "我最近很喜欢看讲得很透的纪录片。"
    assert soul_engine.learn_calls == [
        "cli:我最近很喜欢看讲得很透的纪录片。->我猜你喜欢的是那种能慢慢展开逻辑的讲述方式。"
    ]


@pytest.mark.asyncio
async def test_dialogue_respond_passes_prior_history_to_service() -> None:
    service = FakeService(response="听起来你更在意内容背后的结构和动机。")
    soul_engine = FakeSoulEngine()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=service,
    )
    await dialogue.respond("我喜欢能讲清来龙去脉的视频。")

    await dialogue.respond("尤其是那种会解释为什么会这样的视频。")

    history = service.calls[1]["history"]
    assert history == [
        {"role": "user", "content": "我喜欢能讲清来龙去脉的视频。"},
        {"role": "assistant", "content": "听起来你更在意内容背后的结构和动机。"},
    ]


@pytest.mark.asyncio
async def test_failed_dialogue_rolls_back_history_and_never_learns() -> None:
    service = FakeService(error=LLMResponseContentError("LLM returned an empty response"))
    soul_engine = SimpleNamespace(learn_from_dialogue=AsyncMock())
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=service,
        session="popup",
    )

    with pytest.raises(LLMResponseContentError):
        await dialogue.respond("这是不能被学进去的内容")

    assert dialogue.history == []
    soul_engine.learn_from_dialogue.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_dialogue_rolls_back_history_and_never_learns() -> None:
    started = asyncio.Event()
    blocked = asyncio.Event()

    class BlockingService:
        async def complete_socratic_dialogue(
            self,
            *,
            user_message: str,
            history: list[dict[str, str]],
            caller: str = "",
        ) -> LLMResponse:
            started.set()
            await blocked.wait()
            return LLMResponse(content="不应返回")

    soul_engine = SimpleNamespace(learn_from_dialogue=AsyncMock())
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=BlockingService(),
        session="popup",
    )

    task = asyncio.create_task(dialogue.respond("取消也不能留下历史"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert dialogue.history == []
    soul_engine.learn_from_dialogue.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_typed_failure_does_not_remove_successful_turn() -> None:
    success_started = asyncio.Event()
    release_success = asyncio.Event()
    failure_started = asyncio.Event()
    release_failure = asyncio.Event()
    failure = LLMResponseContentError("LLM returned an empty response")

    class OverlappingService:
        async def complete_socratic_dialogue(
            self,
            *,
            user_message: str,
            history: list[dict[str, str]],
            caller: str = "",
        ) -> LLMResponse:
            if user_message == "成功消息":
                success_started.set()
                await release_success.wait()
                return LLMResponse(content="成功回复")
            failure_started.set()
            await release_failure.wait()
            raise failure

    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=FakeSoulEngine(),
        llm_service=OverlappingService(),
    )
    success_task = asyncio.create_task(dialogue.respond("成功消息"))
    await success_started.wait()
    failure_task = asyncio.create_task(dialogue.respond("失败消息"))
    await asyncio.sleep(0)
    assert not failure_task.done()

    release_success.set()
    assert await success_task == "成功回复"
    await failure_started.wait()
    release_failure.set()
    with pytest.raises(LLMResponseContentError) as raised:
        await failure_task

    assert raised.value is failure
    assert [(turn.role, turn.content) for turn in dialogue.history] == [
        ("user", "成功消息"),
        ("agent", "成功回复"),
    ]


@pytest.mark.asyncio
async def test_concurrent_cancellation_does_not_orphan_successful_tool_turn() -> None:
    cancelled_started = asyncio.Event()
    release_cancelled = asyncio.Event()
    success_started = asyncio.Event()
    release_success = asyncio.Event()

    class OverlappingToolService:
        async def complete_with_tools(self, **kwargs: object) -> LLMResponse:
            user_message = str(kwargs["user_input"])
            if user_message == "取消消息":
                cancelled_started.set()
                await release_cancelled.wait()
                return LLMResponse(content="不应返回")
            success_started.set()
            await release_success.wait()
            return LLMResponse(content="工具路径成功回复")

    soul_engine = SimpleNamespace(learn_from_dialogue=AsyncMock())
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul_engine,
        llm_service=OverlappingToolService(),
        tools=[{"name": "noop"}],
        tool_dispatcher=object(),
    )
    cancelled_task = asyncio.create_task(dialogue.respond("取消消息"))
    await cancelled_started.wait()
    success_task = asyncio.create_task(dialogue.respond("成功消息"))
    await asyncio.sleep(0)
    assert not success_task.done()

    cancelled_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_task
    await success_started.wait()
    release_success.set()
    assert await success_task == "工具路径成功回复"
    await asyncio.sleep(0)

    assert [(turn.role, turn.content) for turn in dialogue.history] == [
        ("user", "成功消息"),
        ("agent", "工具路径成功回复"),
    ]
    soul_engine.learn_from_dialogue.assert_awaited_once_with(
        user_message="成功消息",
        assistant_reply="工具路径成功回复",
        session="cli",
    )


@pytest.mark.asyncio
async def test_cancellation_while_waiting_does_not_start_or_mutate_turn() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingService:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def complete_socratic_dialogue(
            self,
            *,
            user_message: str,
            history: list[dict[str, str]],
            caller: str = "",
        ) -> LLMResponse:
            self.messages.append(user_message)
            started.set()
            await release.wait()
            return LLMResponse(content="成功回复")

    service = BlockingService()
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=FakeSoulEngine(),
        llm_service=service,
    )
    success_task = asyncio.create_task(dialogue.respond("成功消息"))
    await started.wait()
    waiting_task = asyncio.create_task(dialogue.respond("等待时取消"))
    await asyncio.sleep(0)

    waiting_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting_task
    release.set()
    assert await success_task == "成功回复"

    assert service.messages == ["成功消息"]
    assert [(turn.role, turn.content) for turn in dialogue.history] == [
        ("user", "成功消息"),
        ("agent", "成功回复"),
    ]


def test_dialogue_clear_history_resets_turns() -> None:
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=FakeSoulEngine(),
        llm_service=FakeService(response="我们继续。"),
    )
    dialogue._history.extend(  # type: ignore[attr-defined]
        [
            DialogueTurn(role="user", content="hi"),
            DialogueTurn(role="agent", content="hello"),
        ]
    )
    dialogue.clear_history()

    assert dialogue.history == []


def test_dialogue_reuses_soul_engine_service_identity() -> None:
    shared_service = FakeService(response="共享")
    soul_engine = FakeSoulEngine()
    soul_engine._llm_service = shared_service  # type: ignore[attr-defined]
    dialogue = SocraticDialogue(llm=object(), soul_engine=soul_engine)

    assert dialogue._build_service() is shared_service


def test_dialogue_fallback_service_inherits_soul_engine_module_overrides() -> None:
    overrides = {"soul": ModuleOverride(provider="claude", model="claude-sonnet")}
    registry = SimpleNamespace(default_provider="openai")
    soul_engine = SimpleNamespace(_memory=object(), _module_overrides=overrides)
    dialogue = SocraticDialogue(llm=registry, soul_engine=soul_engine)  # type: ignore[arg-type]

    service = dialogue._build_service()

    assert service.module_overrides == overrides
