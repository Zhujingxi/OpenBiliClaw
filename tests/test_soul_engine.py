from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.engine import SoulEngine

if TYPE_CHECKING:
    from pathlib import Path


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


@pytest.mark.asyncio
async def test_analyze_events_updates_preference_layer(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    registry = FakeRegistry(
        json.dumps(
            {
                "interests": [
                    {"name": "历史", "category": "知识", "weight": 0.82, "source": "events"}
                ],
                "favorite_up_users": ["小约翰可汗"],
                "exploration_openness": 0.63,
            },
            ensure_ascii=False,
        )
    )
    engine = SoulEngine(llm=registry, memory=memory)

    await engine.analyze_events(
        [
            {"event_type": "view", "title": "世界史解说"},
            {"event_type": "search", "title": "纪录片推荐", "metadata": {"keyword": "纪录片"}},
        ]
    )

    preference = memory.get_layer("preference").data
    assert preference["interests"][0]["name"] == "历史"
    assert preference["favorite_up_users"] == ["小约翰可汗"]

    saved = json.loads((tmp_path / "memory" / "preference.json").read_text(encoding="utf-8"))
    assert saved["interests"][0]["name"] == "历史"
    assert registry.calls
