"""Tests for Socratic dialogue integration."""

from __future__ import annotations

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.service import LLMServiceError
from openbiliclaw.soul.dialogue import DialogueTurn, SocraticDialogue


class FakeSoulEngine:
    """Minimal soul engine stub for dialogue tests."""


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
    ) -> LLMResponse:
        self.calls.append({"user_message": user_message, "history": history})
        if self.error is not None:
            raise self.error
        return LLMResponse(content=self.response or "", provider="openai")


@pytest.mark.asyncio
async def test_dialogue_respond_appends_user_and_agent_turns() -> None:
    service = FakeService(response="我猜你喜欢的是那种能慢慢展开逻辑的讲述方式。")
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=FakeSoulEngine(),
        llm_service=service,
    )

    reply = await dialogue.respond("我最近很喜欢看讲得很透的纪录片。")

    assert "讲述方式" in reply
    assert len(dialogue.history) == 2
    assert dialogue.history[0].role == "user"
    assert dialogue.history[1].role == "agent"
    assert service.calls[0]["user_message"] == "我最近很喜欢看讲得很透的纪录片。"


@pytest.mark.asyncio
async def test_dialogue_respond_passes_prior_history_to_service() -> None:
    service = FakeService(response="听起来你更在意内容背后的结构和动机。")
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=FakeSoulEngine(),
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
async def test_dialogue_respond_returns_graceful_fallback_on_service_error() -> None:
    service = FakeService(error=LLMServiceError("provider down"))
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=FakeSoulEngine(),
        llm_service=service,
    )

    reply = await dialogue.respond("我有点说不清自己最近为什么总在刷同一类视频。")

    assert "换个说法" in reply
    assert len(dialogue.history) == 2
    assert dialogue.history[1].content == reply


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
