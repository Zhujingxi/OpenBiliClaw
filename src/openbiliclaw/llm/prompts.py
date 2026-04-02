"""Prompt builders for LLM-backed tasks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.soul.tone import ToneProfile


def _render_tone_profile(tone_profile: ToneProfile | None) -> str:
    """Render tone profile guidance for prompt builders."""
    tone = tone_profile or {
        "density": "balanced",
        "warmth": "warm",
        "playfulness": "medium",
        "directness": "balanced",
    }
    return (
        "请保持“老B友”基调：懂 B 站语境，像熟人聊天，不像客服。\n"
        f"- 信息密度: {tone['density']}\n"
        f"- 情绪温度: {tone['warmth']}\n"
        f"- 梗感强度: {tone['playfulness']}\n"
        f"- 直给程度: {tone['directness']}"
    )


def build_socratic_dialogue_prompt(
    *,
    user_message: str,
    core_memory_text: str,
    tone_profile: ToneProfile | None,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Build chat messages for Socratic dialogue generation."""
    system_prompt = "\n\n".join(
        [
            "你是 OpenBiliClaw，一个像朋友一样理解用户的 AI 伙伴。",
            "请使用苏格拉底式对话风格：温和、追问动机、确认理解，但整体更像会接话的老B友，不像客服，也不要像咨询师。",
            _render_tone_profile(tone_profile),
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
5. 所有文本字段（name、category、context 下的 patterns/session_type、disliked_topics）必须用中文。
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
    "weekday_patterns": "工作日集中看 AI 技术资讯和国际时事深度",
    "weekend_patterns": "周末沉浸追番和游戏社区内容",
    "time_of_day_patterns": "深夜到凌晨（2-4点）活跃度最高",
    "session_type": "深度钻研型"
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


def build_soul_profile_prompt(
    *,
    history_summary: dict[str, object],
    preference_summary: dict[str, object],
    recent_awareness: list[dict[str, object]] | None = None,
    active_insights: list[dict[str, object]] | None = None,
    tone_profile: ToneProfile | None,
) -> list[dict[str, str]]:
    """Build a structured prompt for initial soul-profile generation."""
    system_prompt = """
<task>
你要基于用户历史摘要和偏好摘要，生成一份谨慎、温和、像长期观察后的老朋友所写的人格画像。
</task>

<rules>
1. 只能根据给定材料推断，不要做医学化、病理化、断言式结论。
2. 输出必须是严格 JSON，不要附带解释。
3. 人格描述至少 200 个中文字符。
4. core_traits 控制在 3 到 6 条，deep_needs 和 values 保持简洁。
5. 先总结这个人怎么处理信息，再总结他在内容里长期在找什么，最后总结他最近更像处于什么阶段。
6. 不要把兴趣 topic 堆成画像主体；题材、UP 主、作品名最多只举 1 到 2 个例子，
   而且只能当证据，不要当正文主干。
7. 可以参考非临床的认知风格、内在驱动力、阶段状态来组织描述，但不要写理论术语，
   不要写成心理报告、咨询记录或说明书，要像熟人总结这个人的气质和状态。
</rules>

<output_schema>
{
  "personality_portrait": "至少 200 字的自然语言人格描述",
  "core_traits": ["理性", "好奇", "谨慎"],
  "cognitive_style": ["会先看结构", "对证据比较敏感", "偏好把问题讲透"],
  "motivational_drivers": ["建立判断确定性", "持续扩展理解边界"],
  "current_phase": "最近更像在一边吸收高密度信息，一边整理自己的判断框架。",
  "values": ["真实", "成长"],
  "life_stage": "处于探索与积累阶段",
  "deep_needs": ["被理解", "持续成长"]
}
</output_schema>
""".strip()
    system_prompt = "\n\n".join([system_prompt, _render_tone_profile(tone_profile)])
    normalized_awareness = recent_awareness or []
    normalized_insights = active_insights or []
    user_prompt = "\n\n".join(
        [
            "<history_summary>",
            json.dumps(history_summary, ensure_ascii=False, indent=2),
            "</history_summary>",
            "<preference_summary>",
            json.dumps(preference_summary, ensure_ascii=False, indent=2),
            "</preference_summary>",
            "<recent_awareness>",
            json.dumps(normalized_awareness, ensure_ascii=False, indent=2),
            "</recent_awareness>",
            "<active_insights>",
            json.dumps(normalized_insights, ensure_ascii=False, indent=2),
            "</active_insights>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_awareness_prompt(
    *,
    events: list[dict[str, object]],
    preference_summary: dict[str, object],
    soul_profile: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for recent awareness-note generation."""
    system_prompt = """
<task>
你要基于近期用户行为，生成少量谨慎的近期观察笔记。
</task>

<rules>
1. 输出必须是严格 JSON 数组，不要附带解释。
2. observation 只能描述观察到的行为倾向，不要下人格定论。
3. trend 和 emotion_guess 必须使用保守表述。
4. 如果证据不足，可以返回空数组。
</rules>

<output_schema>
[
  {
    "date": "2026-03-08",
    "observation": "最近连续浏览高信息密度内容。",
    "trend": "更偏向深度解释而非轻量消遣。",
    "emotion_guess": "可能处于主动吸收和整理信息的阶段。"
  }
]
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<recent_events>",
            json.dumps(events, ensure_ascii=False, indent=2),
            "</recent_events>",
            "<preference_summary>",
            json.dumps(preference_summary, ensure_ascii=False, indent=2),
            "</preference_summary>",
            "<soul_profile>",
            json.dumps(soul_profile, ensure_ascii=False, indent=2),
            "</soul_profile>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_insight_prompt(
    *,
    awareness_notes: list[dict[str, object]],
    preference_summary: dict[str, object],
    soul_profile: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for insight-hypothesis generation."""
    system_prompt = """
<task>
你要基于近期觉察、偏好摘要和用户画像，生成谨慎的解释性假设。
</task>

<rules>
1. 输出必须是严格 JSON 数组，不要附带解释。
2. hypothesis 是假设，不是结论，措辞必须保守。
3. 每条必须附 1~3 条 evidence。
4. confidence 保持在 0~1，且不要过高。
</rules>

<output_schema>
[
  {
    "hypothesis": "用户可能通过深度内容获得掌控感。",
    "evidence": ["最近连续浏览高信息密度内容。"],
    "confidence": 0.62
  }
]
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<awareness_notes>",
            json.dumps(awareness_notes, ensure_ascii=False, indent=2),
            "</awareness_notes>",
            "<preference_summary>",
            json.dumps(preference_summary, ensure_ascii=False, indent=2),
            "</preference_summary>",
            "<soul_profile>",
            json.dumps(soul_profile, ensure_ascii=False, indent=2),
            "</soul_profile>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_search_queries_prompt(
    *,
    profile_summary: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for search query generation."""
    system_prompt = """
<task>
你要为 B 站内容发现生成一组可搜索的关键词组合。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. query 必须是适合 B 站搜索的短词或短组合，不要写成长句。
3. 优先组合"兴趣主题 + 内容风格/需求"，避免过泛的词。
4. queries 数量控制在 5 到 10 个。
5. 用户画像中包含 interest_domains（一级兴趣域）和 interests（二级具体兴趣）。
   你必须在两个层级之间交替生成 query：
   - 约 40% query 使用一级兴趣域名称搜索（如 "科技 深度" "游戏 机制"），
     目的是发现该域中用户尚未接触的新内容。
   - 约 30% query 使用二级兴趣的细分角度（非直接重复现有词条）。
   - 约 30% query 跨域探索（桥接用户认知风格到陌生领域）。
6. 所有 query 的核心主题词（第一个实词）必须两两不同，
   禁止同一概念换皮出现多次。
</rules>

<output_schema>
{
  "queries": [
    "纪录片 原理",
    "摄影 构图 深度讲解",
    "历史 长视频 深度",
    "认知科学 决策 机制",
    "城市规划 纪录片"
  ]
}
</output_schema>

<examples>
假设用户 interest_domains 包含 [科技(强化学习, ppo), 历史(纪录片)]，
认知风格偏好"结构化分析、高信息密度"：

一级域 query（~40%）：
- "科技 前沿 深度解读"（用域名搜索，覆盖用户未知的科技子领域）
- "历史 冷知识 讲解"（用域名搜索，发现域内新角度）
- "游戏 机制设计 分析"（如果画像有游戏域）

二级细分 query（~30%）：
- "冷战 外交 深度解析"（历史域内的细分角度，非直接重复）
- "强化学习 应用 案例"（具体兴趣的新切面）

跨域探索 query（~30%）：
- "认知科学 决策 机制"（上游学科，桥接：结构化分析偏好）
- "城市规划 发展史 纪录片"（相邻领域，桥接：纪录片风格+系统视角）

坏的 query：
- "强化学习 ppo"（和已有二级兴趣完全重合，无新意）
- "美食"（与用户认知风格无桥接关系，随机发散）
- "博弈论 纳什均衡 策略模型"（三个 query 本质相同，浪费多样性配额）
</examples>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_dialogue_insight_prompt(
    *,
    user_message: str,
    assistant_reply: str,
    core_memory: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for extracting candidate insights from dialogue."""
    system_prompt = """
<task>
你要从一轮用户对话中提取少量高价值的候选理解，用于后续长期画像更新。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. 只提取用户明确表达或高度暗示的稳定信号，不要记录瞬时情绪碎片。
3. kind 只允许: interest, dislike, goal, value, state。
4. confidence 保持保守，0~1。
5. 最多返回 3 条 candidates。
</rules>

<output_schema>
{
  "candidates": [
    {
      "kind": "goal",
      "content": "想更系统地理解国际局势",
      "confidence": 0.84,
      "evidence": "用户明确说想把国际新闻看得更透。"
    }
  ]
}
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<core_memory>",
            json.dumps(core_memory, ensure_ascii=False, indent=2),
            "</core_memory>",
            "<dialogue_turn>",
            json.dumps(
                {
                    "user_message": user_message,
                    "assistant_reply": assistant_reply,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "</dialogue_turn>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_trending_rids_prompt(
    *,
    profile_summary: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for selecting relevant Bilibili ranking rids."""
    system_prompt = """
<task>
你要从用户画像中推断最值得关注的 B 站排行榜分区 rid。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. 只返回 3 到 5 个最相关的分区 rid，不包含 0。
3. 选出的 rid 必须横跨至少 3 个不同的一级分区大类（如知识、科技、影视、生活、游戏等），
   避免全部落在同一大类下，以保证热门内容来源的多样性。
4. 如果不确定，优先选择知识、科技、影视、纪录片相关分区。
</rules>

<output_schema>
{
  "rids": [36, 188, 181, 119]
}
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_content_evaluation_prompt(
    *,
    profile_summary: dict[str, object],
    content_summary: dict[str, object],
    source_context: str = "",
) -> list[dict[str, str]]:
    """Build a structured prompt for content relevance evaluation.

    Args:
        profile_summary: User profile summary.
        content_summary: Content metadata.
        source_context: Discovery context hint (e.g. search / trending / explore).
    """
    source_hint = ""
    if source_context:
        source_hint = (
            "\n<discovery_context>\n"
            f"{source_context}\n"
            "</discovery_context>\n\n"
        )

    system_prompt = (
        "<task>\n"
        + source_hint
        + "你要评估一个 B 站内容与这个用户画像的匹配度。\n"
        "</task>\n\n"
        "<rules>\n"
        "1. 输出必须是严格 JSON，不要附带解释。\n"
        "2. score 范围必须在 0 到 1 之间。\n"
        "3. reason 只写一句中文，解释为什么这个人会喜欢或不喜欢这个内容。\n"
        "4. 不要只说\"因为热门\"或\"因为看过类似的\"，要结合用户画像。\n"
        "5. 根据发现路径调整评判宽容度：search 要求高度匹配；"
        "trending 来源的内容已经过大众验证，只要不在用户讨厌列表中且内容质量过关，基础分应 ≥ 0.6，若还能和画像产生关联则给更高分；"
        "related_chain 允许适度偏移；explore 只要心理需求层面说得通就应该给较高分，即使主题完全陌生也不应因此大幅扣分。\n"
        "6. topic_group 是该内容所属的粗粒度主题分类，用于推荐去重。"
        "要求：2-4 个中文词，抽象到能覆盖同类内容，"
        "例如\"强化学习\"而非\"强化学习ppo算法源码级讲解\"，"
        "\"城市建筑\"而非\"上海外滩建筑群纪录片\"。"
        "同一主题的不同切面必须归为同一个 topic_group。\n"
        "</rules>\n\n"
        "<output_schema>\n"
        "{\n"
        '  "score": 0.78,\n'
        '  "reason": "这个视频的讲解深度和表达方式更贴近你长期偏好的高信息密度内容。",\n'
        '  "topic_group": "认知科学"\n'
        "}\n"
        "</output_schema>"
    )
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
            "<content_summary>",
            json.dumps(content_summary, ensure_ascii=False, indent=2),
            "</content_summary>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_recommendation_expression_prompt(
    *,
    profile_summary: dict[str, object],
    content_summary: dict[str, object],
    tone_profile: ToneProfile | None,
) -> list[dict[str, str]]:
    """Build a structured prompt for friend-style recommendation expression."""
    system_prompt = """
<task>
你要像一个真正懂这个人的老B友一样，给出一段推荐这条 B 站内容的话。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. expression 必须是 50 到 150 字的中文口语表达，像朋友私聊，不像算法推荐。
3. expression 要解释“为什么这条内容会对上这个人的胃口”，不要说空话。
4. topic_label 需要是轻度个性化的主题标签，不要只写泛分类词。
5. 避免机械解释腔、广告腔和“根据你的兴趣”“你可能会喜欢”这类算法套话。
</rules>

<output_schema>
{
  "expression": "这条会对上你最近那种想把问题想透的劲头，"
    "它不是热闹型内容，而是会慢慢把结构给你铺开。",
  "topic_label": "你最近那股想把问题想透的劲头"
}
</output_schema>
""".strip()
    system_prompt = "\n\n".join([system_prompt, _render_tone_profile(tone_profile)])
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
            "<content_summary>",
            json.dumps(content_summary, ensure_ascii=False, indent=2),
            "</content_summary>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_explore_domains_prompt(
    *,
    profile_summary: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for cross-domain exploration ideas."""
    system_prompt = """
<task>
你要为这个用户设计 3 到 5 个“高相关但有陌生感”的跨领域探索方向。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. domain 不能直接重复用户现有高权重兴趣词。
3. domains 至少覆盖 3 类不同内容方向，
   例如知识解释、现实观察、审美体验、人物叙事、技术机制、社会文化；
   不要都落在同一个抽象轴上。
4. 同一母题的换皮变体最多只能保留 1 个，
   例如“博弈论 / 桌游机制 / 纳什均衡 / 策略模型”这类本质相同的方向不能同时出现。
5. why_it_might_resonate 必须先说明它对应用户的哪种认知需求、
   信息处理偏好或内在驱动力，再解释这种陌生内容为什么仍然可能打动这个人。
6. novelty_level 范围必须在 0.4 到 0.8 之间。
7. 每个 domain 生成 1 到 2 个适合 B 站搜索的 query，不能写抽象句子。
8. 不同 domain 的 query 之间词汇重叠率要低；每个 query 必须包含一个内容形式词
   （如 纪录片/深度讲解/科普/测评/vlog/解说/手书/混剪），
   不同 domain 尽量使用不同的形式词，以保证搜索结果在风格维度上有差异。
9. 反信息茧房：不同 domain 的 query 第一个实词（核心主题词）必须两两不同，
   禁止仅替换修饰词而保留相同核心名词；至少 2 个 domain 必须来自用户
   已有兴趣领域之外的全新方向。
</rules>

<output_schema>
{
  "domains": [
    {
      "domain": "城市空间与建筑叙事",
      "why_it_might_resonate": "你偏好结构清晰、能从具体对象看见更大系统的内容。",
      "novelty_level": 0.62,
      "queries": ["城市 建筑 纪录片", "空间 设计 深度讲解"]
    }
  ]
}
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_speculation_generation_prompt(
    *,
    profile_summary: str,
    existing_speculations: list[str],
    cooldown_domains: list[str],
    confirmed_domains: list[str],
    count: int = 5,
) -> list[dict[str, str]]:
    """Build a prompt for generating speculative interest directions."""
    system_prompt = (
        "<task>\n"
        "你是一个用户兴趣探索引擎。根据用户的已确认画像，推测用户可能感兴趣但尚未接触的领域。\n"
        "你需要找到心理学上的桥接关系——从已有兴趣模式中推断出合理的新方向。\n"
        "</task>\n\n"
        "<rules>\n"
        "1. 每个猜测必须有 reason 说明心理学桥接逻辑（为什么从已有兴趣能推出这个新方向）\n"
        "2. 不能重复已有兴趣、已在探索中的方向、或冷却期的方向\n"
        "3. 方向应具体到可以搜索到内容（不要太抽象）\n"
        "4. confidence 范围 0.3-0.6，越有把握越高\n"
        "5. 优先选择跨领域的交叉方向，而非已有兴趣的简单延伸\n"
        "6. 输出严格 JSON，不要附带解释\n"
        "7. 分散性强制要求：\n"
        "   - 所有猜测的 category 必须两两不同，不允许任何两个猜测属于同一大类\n"
        "   - 不同猜测的 domain 核心主题词必须无重叠（禁止同概念换皮）\n"
        "   - 猜测必须横跨至少 3 种不同的认知维度，例如：\n"
        "     知识理解型（科普/历史/哲学）、技能实践型（手工/编程/烹饪）、\n"
        "     审美体验型（音乐/摄影/建筑）、社会观察型（纪录片/人物/社会议题）、\n"
        "     身体感知型（运动/旅行/自然）\n"
        "   - 如果用户兴趣集中在某一维度（如全是知识型），\n"
        "     至少 2 个猜测必须来自其他维度\n"
        "8. 桥接距离要求：\n"
        "   - 至少 1 个猜测是近距离桥接（与已有兴趣共享 1 个属性）\n"
        "   - 至少 1 个猜测是远距离桥接（与已有兴趣仅共享深层心理需求，\n"
        "     表面看不出明显关联）\n"
        "   - 至少 1 个猜测是纯新奇方向（从用户人格特质出发，\n"
        "     而非从现有兴趣出发推理）\n"
        "</rules>\n\n"
        "<bridge_examples>\n"
        "近距离桥接：\n"
        "- 策略游戏 + 数据分析 -> 博弈论科普（共通：系统性思维+决策优化）\n"
        "远距离桥接：\n"
        "- 深度时事解读 + 对因果链的执念 -> 法医学纪录片（共通：追溯真相的思维模式）\n"
        "纯新奇方向：\n"
        "- 用户特质「对精密结构的审美偏好」 -> 机械表拆解/钟表工艺\n"
        "  （不从兴趣出发，而从人格出发：精密结构审美→微观工艺世界）\n\n"
        "坏的示例（太集中）：\n"
        "- 博弈论科普 + 纳什均衡 + 策略模型（本质同一主题）\n"
        "- 认知科学 + 神经科学 + 心理学实验（同一维度的三个变体）\n"
        "</bridge_examples>\n\n"
        "<output_schema>\n"
        "{\n"
        '  "speculations": [\n'
        "    {\n"
        '      "domain": "具体的兴趣方向名称",\n'
        '      "category": "所属大类（必须两两不同）",\n'
        '      "reason": "心理学桥接推理：从X兴趣+Y特质->可能喜欢此方向",\n'
        '      "bridge_type": "near|far|novel",\n'
        '      "confidence": 0.45\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "</output_schema>"
    )

    exclude_list = sorted(set(existing_speculations + cooldown_domains + confirmed_domains))
    exclude_text = "以下方向不要重复：" + "、".join(exclude_list) if exclude_list else "无排除项"
    user_prompt = "\n\n".join([
        "<user_profile>",
        profile_summary,
        "</user_profile>",
        "<exclude_domains>",
        exclude_text,
        "</exclude_domains>",
        f"请生成 {count} 个猜测兴趣方向。",
    ])
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
