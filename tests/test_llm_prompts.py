"""Tests for prompt builders and core memory rendering."""

from pathlib import Path

from openbiliclaw.llm.prompts import build_socratic_dialogue_prompt
from openbiliclaw.memory.manager import MemoryManager


def test_render_core_memory_prompt_includes_soul_and_preferences(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.get_layer("soul").update("personality_portrait", "一个理性又敏感的人")
    memory.get_layer("preference").update("favorite_up_users", ["影视飓风", "小约翰可汗"])

    prompt = memory.render_core_memory_prompt()

    assert "理性又敏感" in prompt
    assert "favorite_up_users" in prompt
    assert "影视飓风" in prompt


def test_render_core_memory_prompt_handles_empty_memory(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)

    prompt = memory.render_core_memory_prompt()

    assert "尚未建立完整画像" in prompt


def test_build_socratic_dialogue_prompt_orders_messages_correctly() -> None:
    messages = build_socratic_dialogue_prompt(
        user_message="我最近有点迷上纪录片",
        core_memory_text="## 用户画像\n喜欢深度内容",
        history=[
            {"role": "user", "content": "我最近总在看长视频"},
            {"role": "assistant", "content": "你更在意信息密度还是叙事感？"},
        ],
    )

    assert messages[0]["role"] == "system"
    assert "喜欢深度内容" in messages[0]["content"]
    assert messages[1]["content"] == "我最近总在看长视频"
    assert messages[2]["content"] == "你更在意信息密度还是叙事感？"
    assert messages[3]["content"] == "我最近有点迷上纪录片"


def test_build_socratic_dialogue_prompt_includes_dialogue_instructions() -> None:
    messages = build_socratic_dialogue_prompt(
        user_message="我喜欢那种讲得很透的内容",
        core_memory_text="（尚未建立完整画像）",
        history=[],
    )

    assert "苏格拉底" in messages[0]["content"]
    assert "像朋友一样" in messages[0]["content"]
