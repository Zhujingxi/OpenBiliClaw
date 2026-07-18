"""Run one real evaluation round using Claude Agent SDK.

Usage:
    .venv/bin/python scripts/run_eval_round.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# Project root setup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


async def main() -> None:
    from openbiliclaw.config import load_config
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.profile import OnionProfile

    print("=" * 60)
    print("画像自迭代 — 第一轮真实评估")
    print("=" * 60)

    # 1. Load config and existing data
    cfg = load_config()
    data_dir = Path(cfg.data_dir)
    memory = MemoryManager(data_dir)
    memory.initialize()

    print("\n[1/5] 加载现有数据...")
    soul_data = memory.get_layer("soul").data
    if not soul_data:
        print("  ❌ 尚未初始化画像，请先运行 openbiliclaw init")
        return

    current_profile = OnionProfile.from_dict(soul_data)
    print(f"  ✅ 当前画像已加载 (onion v{current_profile.version})")
    print(f"  画像概要: {current_profile.personality_portrait[:80]}...")
    print(f"  核心特质: {current_profile.core.core_traits}")
    print(f"  MBTI: {current_profile.core.mbti.type or '未推断'}")

    event_count = len(memory.query_events(limit=10000))
    print(f"  事件总数: {event_count}")

    # 2. Use Agent SDK to generate a "ground truth" persona
    # based on real events — what the profile SHOULD look like
    print("\n[2/5] Agent SDK: 基于真实数据生成参考画像 (ground truth)...")

    from openbiliclaw.eval.agents import ONION_PROFILE_SCHEMA, _collect_text, _extract_json

    # Build a summary of real events for the agent
    recent_events = memory.query_events(limit=200)
    event_summary_parts: list[str] = []
    event_type_counts: dict[str, int] = {}
    titles: list[str] = []
    for ev in recent_events:
        et = str(ev.get("event_type", ""))
        event_type_counts[et] = event_type_counts.get(et, 0) + 1
        title = str(ev.get("title", ""))
        if title and len(titles) < 50:
            titles.append(title)

    event_summary_parts.append(
        "事件类型分布: " + ", ".join(f"{k}:{v}" for k, v in sorted(event_type_counts.items()))
    )
    event_summary_parts.append("最近观看/操作的内容标题（前50条）:")
    for t in titles:
        event_summary_parts.append(f"  - {t}")

    pref_data = memory.get_layer("preference").data
    event_summary_parts.append(
        f"\n当前偏好层数据:\n{json.dumps(pref_data, ensure_ascii=False, indent=2)[:2000]}"
    )
    event_summary = "\n".join(event_summary_parts)

    json.dumps(ONION_PROFILE_SCHEMA, ensure_ascii=False, indent=2)[:1500]

    from claude_agent_sdk import ClaudeAgentOptions

    text = await _collect_text(
        prompt=(
            "根据以下真实的 B 站用户行为数据，推断这个用户应该是什么样的人，"
            "生成一个完整的用户画像。注意：这是真实数据，请认真分析。\n"
            "请返回一个 JSON 代码块（```json），包含以下字段：\n"
            "personality_portrait, core (core_traits, deep_needs, mbti), "
            "values_layer (values, motivational_drivers), "
            "interest (likes 树状, dislikes, favorite_up_users), "
            "role (life_stage, current_phase), "
            "surface (cognitive_style, exploration_openness)\n\n"
            f"{event_summary}"
        ),
        options=ClaudeAgentOptions(
            system_prompt=(
                "你是用户画像分析专家。根据真实行为数据推断用户画像。\n"
                "只返回一个 JSON 代码块，不要其他文字。\n"
                "personality_portrait 至少 200 字，likes 至少 3 个 domain。"
            ),
            max_turns=1,
        ),
    )

    try:
        gt_data = _extract_json(text)
        ground_truth_profile = OnionProfile.from_dict(gt_data)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"  ❌ 无法解析参考画像: {exc}")
        print(f"  原始返回: {text[:300]}...")
        return

    print("  ✅ 参考画像已生成")
    print(f"  核心特质: {ground_truth_profile.core.core_traits}")
    print(f"  MBTI: {ground_truth_profile.core.mbti.type or '未推断'}")
    if ground_truth_profile.interest.likes:
        domains = [d.domain for d in ground_truth_profile.interest.likes[:5]]
        print(f"  兴趣宽域: {domains}")

    # 3. Evaluate current profile against ground truth
    print("\n[3/5] 逐层逐字段评估当前画像...")
    from openbiliclaw.eval.evaluator import ProfileEvaluator

    evaluator = ProfileEvaluator()
    eval_report = await evaluator.evaluate(ground_truth_profile, current_profile)

    print(f"  整体评分: {eval_report.overall_score:.3f}")
    for ls in eval_report.layer_scores:
        icon = "✅" if ls.score >= 0.8 else "⚠️" if ls.score >= 0.5 else "❌"
        print(f"  {icon} {ls.layer}: {ls.score:.3f}")
        for fs in ls.field_scores:
            if fs.score < 0.9:
                si = "⚠️" if fs.score >= 0.5 else "❌"
                dev = f" — {fs.deviation[:60]}" if fs.deviation else ""
                print(f"      {si} {fs.field}: {fs.score:.2f}{dev}")

    # 4. Use Agent SDK Eval Agent for semantic analysis
    print("\n[4/5] Agent SDK: 语义级评估...")
    from openbiliclaw.eval.agents import run_eval_agent

    semantic_eval = await run_eval_agent(ground_truth_profile, current_profile)
    semantic_score = semantic_eval.get("overall_score", 0)
    print(f"  语义评分: {semantic_score:.3f}" if isinstance(semantic_score, (int, float)) else f"  语义评分: {semantic_score}")

    semantic_attrs = semantic_eval.get("attributions", [])
    if semantic_attrs:
        print("  归因分析:")
        for attr in semantic_attrs[:5]:
            print(f"    - {attr}")

    # 5. Use Agent SDK Optimizer to propose improvements
    print("\n[5/5] Agent SDK: 提出优化建议...")

    combined_report = eval_report.to_dict()
    combined_report["semantic_eval"] = semantic_eval

    from openbiliclaw.eval.agents import run_optimizer_agent

    optimization = await run_optimizer_agent(combined_report, PROJECT_ROOT)

    changes = optimization.get("changes", [])
    summary = optimization.get("summary", "无建议")
    print(f"  建议总结: {summary}")
    print(f"  建议修改数: {len(changes)}")
    for i, change in enumerate(changes, 1):
        print(f"\n  修改 {i}:")
        print(f"    文件: {change.get('file_path', 'unknown')}")
        print(f"    原因: {change.get('reason', 'unknown')}")
        old = str(change.get("old_text", ""))[:80]
        new = str(change.get("new_text", ""))[:80]
        print(f"    旧文本: {old}...")
        print(f"    新文本: {new}...")

    # Save results
    report_dir = data_dir / "eval" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")

    report_path = report_dir / f"real_eval_{timestamp}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": timestamp,
            "event_count": event_count,
            "eval_report": combined_report,
            "optimization": optimization,
            "ground_truth_portrait": ground_truth_profile.personality_portrait[:500],
            "current_portrait": current_profile.personality_portrait[:500],
        }, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print(f"评估完成！报告已保存到: {report_path}")
    print(f"{'=' * 60}")

    # Print summary
    from openbiliclaw.eval.report import render_eval_report
    print("\n" + render_eval_report(eval_report.to_dict()))


if __name__ == "__main__":
    asyncio.run(main())
