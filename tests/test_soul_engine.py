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


@pytest.mark.asyncio
async def test_build_initial_profile_reads_preference_and_saves_soul(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("preference").data.update(
        {
            "interests": [{"name": "科技", "category": "知识", "weight": 0.81}],
            "favorite_up_users": ["老师好我叫何同学"],
        }
    )
    registry = FakeRegistry(
        json.dumps(
            {
                "personality_portrait": "这个人会反复在高信息密度内容里停留，也会主动寻找讲清原理的表达方式。"
                * 8,
                "core_traits": ["理性", "好奇", "克制"],
                "values": ["成长", "真实"],
                "life_stage": "处于探索与积累阶段",
                "deep_needs": ["被理解", "持续成长"],
            },
            ensure_ascii=False,
        )
    )
    engine = SoulEngine(llm=registry, memory=memory)

    profile = await engine.build_initial_profile(
        history=[
            {"title": "AI 工具实测", "author": "科技UP主"},
            {"title": "效率系统分享", "author": "知识UP主"},
        ]
    )

    assert profile.core_traits == ["理性", "好奇", "克制"]
    saved = json.loads((tmp_path / "memory" / "soul.json").read_text(encoding="utf-8"))
    assert saved["core_traits"] == ["理性", "好奇", "克制"]
    assert saved["preferences"]["interests"][0]["name"] == "科技"


@pytest.mark.asyncio
async def test_get_profile_loads_saved_soul_profile(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update(
        {
            "personality_portrait": "这是一个偏爱深度内容、对信息质量较敏感、做决定前会先观察的人。" * 8,
            "core_traits": ["理性", "谨慎", "自驱"],
            "values": ["真实", "成长"],
            "life_stage": "稳定积累阶段",
            "deep_needs": ["被理解", "保持成长"],
            "preferences": {"interests": [{"name": "科技", "category": "知识", "weight": 0.8}]},
        }
    )
    memory.get_layer("soul").save()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    profile = await engine.get_profile()

    assert profile.core_traits == ["理性", "谨慎", "自驱"]
    assert profile.preferences.interests[0].name == "科技"


@pytest.mark.asyncio
async def test_get_profile_raises_when_soul_not_initialized(tmp_path: Path) -> None:
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=FakeRegistry("{}"), memory=memory)

    with pytest.raises(SoulProfileNotInitializedError):
        await engine.get_profile()
