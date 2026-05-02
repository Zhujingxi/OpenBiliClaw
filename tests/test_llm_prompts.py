"""Tests for prompt builders and core memory rendering."""

from pathlib import Path

from openbiliclaw.llm.prompts import (
    build_explore_domains_prompt,
    build_recommendation_expression_prompt,
    build_socratic_dialogue_prompt,
    build_soul_profile_prompt,
)
from openbiliclaw.memory.manager import MemoryManager


def test_render_core_memory_prompt_includes_soul_and_preferences(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.get_layer("soul").update("personality_portrait", "一个理性又敏感的人")
    memory.get_layer("preference").update("favorite_up_users", ["影视飓风", "小约翰可汗"])

    prompt = memory.render_core_memory_prompt()

    assert "理性又敏感" in prompt
    assert "常看UP主" in prompt
    assert "影视飓风" in prompt


def test_render_core_memory_prompt_handles_empty_memory(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)

    prompt = memory.render_core_memory_prompt()

    assert "尚未建立完整画像" in prompt


def test_build_socratic_dialogue_prompt_orders_messages_correctly() -> None:
    messages = build_socratic_dialogue_prompt(
        user_message="我最近有点迷上纪录片",
        core_memory_text="## 用户画像\n喜欢深度内容",
        tone_profile={
            "density": "dense",
            "warmth": "warm",
            "playfulness": "medium",
            "directness": "balanced",
        },
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
        tone_profile={
            "density": "dense",
            "warmth": "warm",
            "playfulness": "medium",
            "directness": "balanced",
        },
        history=[],
    )

    assert "苏格拉底" in messages[0]["content"]
    assert "老B友" in messages[0]["content"]


def test_build_recommendation_expression_prompt_mentions_old_friend_tone() -> None:
    """v0.3.28+: tone-profile rendering with 老B友 lives in user_prompt
    instead of system_prompt. System keeps the algorithm-rejection rule."""
    messages = build_recommendation_expression_prompt(
        profile_summary={"personality_portrait": "偏好高信息密度内容"},
        content_summary={"title": "讲透国际局势", "up_name": "某UP"},
        tone_profile={
            "density": "dense",
            "warmth": "warm",
            "playfulness": "medium",
            "directness": "balanced",
        },
        source_platform="bilibili",
    )

    # 老B友 now in user_prompt's tone block (not system)
    assert "老B友" in messages[1]["content"]
    # System keeps the algorithm-recommendation taboo
    assert "不像算法推荐" in messages[0]["content"]


def test_build_soul_profile_prompt_avoids_report_tone() -> None:
    messages = build_soul_profile_prompt(
        history_summary={"recent_topics": ["国际新闻"]},
        preference_summary={"interests": ["国际关系"]},
        tone_profile={
            "density": "dense",
            "warmth": "warm",
            "playfulness": "medium",
            "directness": "balanced",
        },
    )

    assert "朋友" in messages[0]["content"]
    assert "3 到 6 条" in messages[0]["content"]


def test_build_explore_domains_prompt_requires_directional_diversity() -> None:
    messages = build_explore_domains_prompt(
        profile_summary={
            "personality_portrait": "偏好把复杂问题讲透，也愿意接受有陌生感的新内容。",
            "interests": ["策略游戏", "深度讲解"],
            "deep_needs": ["建立判断确定性"],
        }
    )

    system_prompt = messages[0]["content"]

    assert "至少覆盖 3 类不同内容方向" in system_prompt
    assert "同一母题的换皮变体最多只能保留 1 个" in system_prompt
    assert "先说明它对应用户的哪种认知需求" in system_prompt


def test_build_explore_domains_prompt_requires_core_interest_anchors() -> None:
    messages = build_explore_domains_prompt(
        profile_summary={
            "personality_portrait": "偏好高信息密度内容，也接受适度陌生感。",
            "interests": ["咒术回战", "Fate", "AI技术与大模型"],
            "deep_needs": ["建立判断确定性"],
        }
    )

    system_prompt = messages[0]["content"]

    assert "domain" in system_prompt
    assert "novelty_level" in system_prompt
    assert "why_it_might_resonate" in system_prompt


# ----------------------------------------------------------------------
# v0.3.28+: prompt-cache convention enforcement.
#
# All prompt builders MUST emit a system message that's byte-identical
# across different per-call inputs. Provider-side prompt cache (DeepSeek,
# OpenAI, Claude, Gemini, most relays) only fires when the prefix is
# completely stable; any builder that interpolates per-call data into
# the system message effectively turns off caching for every call.
#
# Contract: system_prompt is a function ONLY of the prompt template
# itself, never of the call arguments. Verify by calling each builder
# with two distinctly-different argument sets and asserting the system
# message is identical.


def _builder_test_inputs() -> list[tuple[str, dict, dict]]:
    """(builder_name, args1, args2) — two materially different inputs each.

    Add a row here when introducing a new prompt builder; the test below
    will then guard its system-prompt stability automatically.
    """
    return [
        (
            "build_batch_content_evaluation_prompt",
            dict(
                profile_summary={"a": 1},
                content_items=[{"x": 1}],
                source_context="search",
                source_platform="bilibili",
            ),
            dict(
                profile_summary={"a": 2},
                content_items=[{"x": 2}],
                source_context="trending",
                source_platform="xiaohongshu",
            ),
        ),
        (
            "build_content_evaluation_prompt",
            dict(
                profile_summary={"a": 1},
                content_summary={"x": 1},
                source_context="search",
                source_platform="bilibili",
            ),
            dict(
                profile_summary={"a": 2},
                content_summary={"x": 2},
                source_context="explore",
                source_platform="xiaohongshu",
            ),
        ),
        (
            "build_recommendation_expression_prompt",
            dict(
                profile_summary={"a": 1},
                content_summary={"x": 1},
                tone_profile=None,
                source_platform="bilibili",
            ),
            dict(
                profile_summary={"a": 2},
                content_summary={"x": 2},
                tone_profile={
                    "density": "dense",
                    "warmth": "warm",
                    "playfulness": "low",
                    "directness": "direct",
                },
                source_platform="xiaohongshu",
            ),
        ),
        (
            "build_batch_expression_prompt",
            dict(
                profile_summary={"a": 1},
                content_items=[{"x": 1}],
                tone_profile=None,
                source_platform="bilibili",
            ),
            dict(
                profile_summary={"a": 2},
                content_items=[{"x": 2}],
                tone_profile={
                    "density": "balanced",
                    "warmth": "neutral",
                    "playfulness": "high",
                    "directness": "balanced",
                },
                source_platform="xiaohongshu",
            ),
        ),
        (
            "build_delight_reason_prompt",
            dict(
                profile_summary={"a": 1},
                content_summary={"x": 1},
                reason_stub="x",
                tone_profile=None,
                source_platform="bilibili",
            ),
            dict(
                profile_summary={"a": 2},
                content_summary={"x": 2},
                reason_stub="y",
                tone_profile={
                    "density": "dense",
                    "warmth": "warm",
                    "playfulness": "medium",
                    "directness": "balanced",
                },
                source_platform="xiaohongshu",
            ),
        ),
        # NOTE: build_socratic_dialogue_prompt is intentionally NOT in
        # this list — its system prompt embeds per-user core memory /
        # tone / friend label, which is fine for OpenBiliClaw's single-
        # user model (per-user state is stable across sessions for the
        # same install, so cache still fires on repeated dialogue
        # turns). A multi-user deployment would refactor it.
    ]


def test_prompt_builder_system_messages_are_call_invariant() -> None:
    """Every prompt builder must emit a system message that does NOT
    depend on per-call arguments. Required for provider-side prompt
    cache to actually hit.

    If this test fails for a NEW builder you just added: refactor so
    the variables move to user_prompt and only the static template
    stays in system. See ``build_batch_content_evaluation_prompt`` for
    the canonical pattern.
    """
    from openbiliclaw.llm import prompts as prompts_mod

    failures: list[str] = []
    for name, args1, args2 in _builder_test_inputs():
        fn = getattr(prompts_mod, name, None)
        assert fn is not None, f"missing builder: {name}"
        m1 = fn(**args1)
        m2 = fn(**args2)
        assert m1 and m1[0].get("role") == "system", f"{name}: no system msg"
        sys1 = m1[0]["content"]
        sys2 = m2[0]["content"]
        if sys1 != sys2:
            failures.append(name)

    assert not failures, (
        "Cache-poisoning prompt builders (system message changed with "
        "input — extends provider cache miss across all calls): "
        f"{failures}. Refactor to put per-call variables in user_prompt."
    )
