"""Prompt builders for LLM-backed tasks."""

from __future__ import annotations

import json


def build_socratic_dialogue_prompt(
    *,
    user_message: str,
    core_memory_text: str,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Build chat messages for Socratic dialogue generation."""
    system_prompt = "\n\n".join(
        [
            "你是 OpenBiliClaw，一个像朋友一样理解用户的 AI 伙伴。",
            "请使用苏格拉底式对话风格：温和、追问动机、确认理解，不要像客服。",
            "以下是当前用户的 core memory，请把它作为理解用户的背景，而不是机械复述：",
            core_memory_text,
        ]
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    return messages


def render_preference_summary(preference_summary: dict[str, object]) -> str:
    """Render preference summary into stable text."""
    if not preference_summary:
        return "（暂无偏好摘要）"
    return json.dumps(preference_summary, ensure_ascii=False, indent=2)


def build_preference_analysis_prompt(
    *,
    events: list[dict[str, object]],
    existing_preference: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for extracting user preferences from events."""
    system_prompt = """
<task>
你要从一批用户行为事件中提取稳定偏好画像。
</task>

<rules>
1. 只能根据提供的事件推断，不要猜测没有证据的结论。
2. 输出必须是严格 JSON，不要附带解释。
3. 如果证据不足，返回空数组、默认值或较低权重。
4. 兴趣标签控制在 5~15 个以内，weight 在 0~1 之间。
</rules>

<output_schema>
{
  "interests": [{"name": "历史", "category": "知识", "weight": 0.8, "source": "watch history"}],
  "style": {
    "preferred_duration": "long",
    "preferred_pace": "moderate",
    "quality_sensitivity": 0.5,
    "humor_preference": 0.3,
    "depth_preference": 0.9
  },
  "context": {
    "weekday_patterns": "",
    "weekend_patterns": "",
    "time_of_day_patterns": "",
    "session_type": "deep_dive"
  },
  "exploration_openness": 0.6,
  "disliked_topics": ["低质标题党"],
  "favorite_up_users": ["某个UP主"]
}
</output_schema>

<examples>
输入事件里如果多次出现长视频、纪录片、深度讲解，
可以提高 “历史/纪录片/知识” 相关标签和 depth_preference。
</examples>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<existing_preference>",
            json.dumps(existing_preference, ensure_ascii=False, indent=2),
            "</existing_preference>",
            "<event_batch>",
            json.dumps(events, ensure_ascii=False, indent=2),
            "</event_batch>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
