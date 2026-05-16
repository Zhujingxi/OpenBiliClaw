from __future__ import annotations

import json

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.profile import AwarenessNote


class FakeRegistry:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict[str, str]]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=self.content, provider="openai")


class FakeStructuredService:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse:
        self.calls.append({"system_instruction": system_instruction, "user_input": user_input})
        return LLMResponse(content=self.content, provider="openai")


@pytest.mark.asyncio
async def test_awareness_analyzer_builds_notes_from_recent_events() -> None:
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    service = FakeStructuredService(
        json.dumps(
            [
                {
                    "date": "2026-03-08",
                    "observation": "最近连续浏览高信息密度内容。",
                    "trend": "更偏向深度解释而非轻量消遣。",
                    "emotion_guess": "可能处于主动吸收和整理信息的阶段。",
                }
            ],
            ensure_ascii=False,
        )
    )

    notes = await AwarenessAnalyzer(service).analyze(
        events=[{"event_type": "view", "title": "AI 工具实测"}],
        preference={},
        soul_profile={},
    )

    assert notes[0].observation.startswith("最近连续浏览")
    assert notes[0].trend.startswith("更偏向深度解释")
    assert service.calls


@pytest.mark.asyncio
async def test_awareness_analyzer_accepts_object_wrapped_notes() -> None:
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    service = FakeStructuredService(
        json.dumps(
            {
                "results": [
                    {
                        "date": "2026-03-08",
                        "observation": "最近连续浏览高信息密度内容。",
                        "trend": "更偏向深度解释而非轻量消遣。",
                        "emotion_guess": "可能处于主动吸收和整理信息的阶段。",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )

    notes = await AwarenessAnalyzer(service).analyze(
        events=[{"event_type": "view", "title": "AI 工具实测"}],
        preference={},
        soul_profile={},
    )

    assert notes == [
        AwarenessNote(
            date="2026-03-08",
            observation="最近连续浏览高信息密度内容。",
            trend="更偏向深度解释而非轻量消遣。",
            emotion_guess="可能处于主动吸收和整理信息的阶段。",
        )
    ]


@pytest.mark.asyncio
async def test_awareness_analyzer_raises_on_invalid_json() -> None:
    from openbiliclaw.soul.awareness_analyzer import (
        AwarenessAnalyzer,
        AwarenessGenerationError,
    )

    analyzer = AwarenessAnalyzer(FakeStructuredService("not-json"))
    with pytest.raises(AwarenessGenerationError, match="invalid JSON"):
        await analyzer.analyze(
            events=[{"event_type": "view", "title": "AI 工具实测"}],
            preference={},
            soul_profile={},
        )


def test_merge_awareness_notes_deduplicates_same_day_observation() -> None:
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    analyzer = AwarenessAnalyzer(FakeStructuredService("[]"))
    existing = [
        AwarenessNote(
            date="2026-03-08",
            observation="最近连续浏览高信息密度内容。",
            trend="更偏向深度解释。",
            emotion_guess="专注",
        )
    ]
    incoming = [
        AwarenessNote(
            date="2026-03-08",
            observation="最近连续浏览高信息密度内容。",
            trend="更偏向深度解释而非轻量消遣。",
            emotion_guess="专注",
        )
    ]

    merged = analyzer.merge_notes(existing, incoming)

    assert len(merged) == 1


@pytest.mark.asyncio
async def test_awareness_analyzer_can_use_unified_service() -> None:
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    service = FakeStructuredService(
        json.dumps(
            [
                {
                    "date": "2026-03-08",
                    "observation": "最近更专注。",
                    "trend": "更偏向深度浏览。",
                    "emotion_guess": "可能在主动整理信息。",
                }
            ],
            ensure_ascii=False,
        )
    )

    notes = await AwarenessAnalyzer(service).analyze(
        events=[{"event_type": "view", "title": "AI 视频"}],
        preference={},
        soul_profile={},
    )

    assert notes[0].observation == "最近更专注。"
    assert service.calls


def test_awareness_analyzer_requires_core_memory_task_service() -> None:
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer

    with pytest.raises(TypeError, match="complete_structured_task"):
        AwarenessAnalyzer(FakeRegistry("[]"))
