"""Run init profile generation from real Bilibili data for human evaluation.

All raw inputs, prompts, LLM responses, and intermediate results are
logged to data/eval/runs/init_human_<timestamp>/.

Usage:
    .venv/bin/python scripts/run_init_eval.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


async def main() -> None:
    from openbiliclaw.bilibili.api import BilibiliAPIClient
    from openbiliclaw.bilibili.auth import resolve_runtime_cookie
    from openbiliclaw.config import load_config
    from openbiliclaw.eval.run_logger import RunLogger
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine
    from _model_runtime import build_script_model_bundle

    cfg = load_config()
    data_dir = cfg.data_path

    # Initialize run logger
    rl = RunLogger(task="init_human", data_dir=data_dir)

    print("=" * 60)
    print("从零生成画像 (init) — B站真实数据")
    print(f"日志目录: {rl.run_dir}")
    print("=" * 60)

    memory = MemoryManager(data_dir)
    memory.initialize()

    cookie = resolve_runtime_cookie(
        data_dir=data_dir,
        configured_cookie=cfg.bilibili.cookie,
    )
    client = BilibiliAPIClient(cookie=cookie)

    # --- 1. Fetch and log raw inputs ---
    input_step = rl.step("input")

    print("\n[1/4] 拉取浏览历史...")
    history = await client.get_user_history(max_items=500)
    print(f"  ✅ {len(history)} 条浏览历史")
    input_step.save_json("history.json", history)

    print("\n[2/4] 拉取收藏夹...")
    favorites_data: list[dict[str, Any]] = []
    try:
        fav_folders = await client.get_all_favorites(
            max_folders=20, max_items_per_folder=200,
        )
        for folder in fav_folders:
            folder_title = folder.folder.title if hasattr(folder, "folder") else "未知"
            count = len(folder.items) if hasattr(folder, "items") else 0
            print(f"  📁 {folder_title}: {count} 项")
            for item in (folder.items if hasattr(folder, "items") else []):
                favorites_data.append({
                    "title": getattr(item, "title", str(item)),
                    "upper": getattr(item, "upper", ""),
                    "folder": folder_title,
                })
        print(f"  ✅ 共 {len(favorites_data)} 个收藏")
    except Exception as exc:
        print(f"  ⚠️ 收藏夹拉取失败: {exc}")
    input_step.save_json("favorites.json", favorites_data)

    print("\n[3/4] 拉取关注列表...")
    following_data: list[dict[str, Any]] = []
    try:
        for page in range(1, 6):
            page_users = await client.get_following(page=page, page_size=50)
            if not page_users:
                break
            for user in page_users:
                name = getattr(user, "uname", str(user))
                sign = getattr(user, "sign", "")
                following_data.append({"name": name, "sign": sign})
            print(f"  第 {page} 页: {len(page_users)} 人")
            if len(page_users) < 50:
                break
        print(f"  ✅ 共 {len(following_data)} 个关注")
    except Exception as exc:
        print(f"  ⚠️ 关注列表拉取失败: {exc}")
    input_step.save_json("following.json", following_data)

    if not history:
        print("\n  ❌ 浏览历史为空")
        return

    # --- 2. Analyze preferences (log prompt + response) ---
    pref_step = rl.step("preference")

    events: list[dict[str, Any]] = []
    for item in history:
        h = item.get("history", {}) or {}
        bvid = str(h.get("bvid", ""))
        events.append({
            "event_type": "view",
            "title": str(item.get("title", "")),
            "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
            "metadata": {
                "bvid": bvid,
                "author": str(item.get("author_name", item.get("author", ""))),
            },
        })
    for fav in favorites_data:
        events.append({
            "event_type": "favorite",
            "title": str(fav.get("title", "")),
            "metadata": {"folder": str(fav.get("folder", "")), "upper": str(fav.get("upper", ""))},
        })
    pref_step.save_json("events_input.json", events[:50])  # Save sample

    # Log the preference analysis prompt
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt
    pref_prompt_msgs = build_preference_analysis_prompt(
        events=events[:20], existing_preference={},
    )
    pref_step.save_prompt(pref_prompt_msgs)

    print("\n[4/4] 分析偏好 + 生成画像...")
    print(f"  总信号量: {len(events)} 条事件")

    model_bundle = build_script_model_bundle(cfg, memory)
    engine = SoulEngine(
        llm=model_bundle.chat_route,
        memory=memory,
        embedding_service=model_bundle.embedding_service,
    )

    await engine.analyze_events(events)

    # Save preference result
    pref_result = dict(memory.get_layer("preference").data)
    pref_step.save_json("result.json", pref_result)

    # --- 3. Build profile (log prompt + response) ---
    profile_step = rl.step("profile")

    combined_history = list(history)
    if favorites_data:
        combined_history.append({
            "title": "[收藏夹汇总]",
            "_favorites": favorites_data,
            "_favorites_summary": f"共 {len(favorites_data)} 个收藏，"
            + "涵盖: " + ", ".join(
                set(f.get("folder", "") for f in favorites_data[:100] if f.get("folder"))
            ),
        })
    if following_data:
        combined_history.append({
            "title": "[关注列表汇总]",
            "_following": following_data,
            "_following_summary": f"共关注 {len(following_data)} 人，"
            + "包括: " + ", ".join(f["name"] for f in following_data[:100]),
        })

    # Log the profile generation prompt
    from openbiliclaw.llm.prompts import build_soul_profile_prompt
    from openbiliclaw.soul.profile_builder import ProfileBuilder
    profile_prompt_msgs = build_soul_profile_prompt(
        history_summary=ProfileBuilder._summarize_history(combined_history),
        preference_summary=pref_result,
        recent_awareness=[],
        active_insights=[],
        tone_profile=None,
    )
    profile_step.save_prompt(profile_prompt_msgs)

    profile = await engine.build_initial_profile(combined_history)
    memory.save_all()

    # Save profile result
    profile_step.save_json("result.json", profile.to_dict())

    # --- 4. Display for human evaluation ---
    display_step = rl.step("display")

    print("\n" + "=" * 60)
    print("画像生成完成 — 请逐层评测")
    print("=" * 60)

    output_lines: list[str] = []

    def _print_and_log(text: str) -> None:
        print(text)
        output_lines.append(text)

    _print_and_log("\n━━━ 核心层 Core ━━━")
    _print_and_log("  人格特质:")
    for i, t in enumerate(profile.core.core_traits, 1):
        _print_and_log(f"    {i}. {t}")
    _print_and_log("  深层需求:")
    for i, n in enumerate(profile.core.deep_needs, 1):
        _print_and_log(f"    {i}. {n}")
    mbti = profile.core.mbti
    if mbti.type:
        _print_and_log(f"  MBTI: {mbti.type} (置信度: {mbti.confidence:.0%})")
        for k, d in mbti.dimensions.items():
            _print_and_log(f"    {k}: {d.pole} ({d.strength:.2f})")
    else:
        _print_and_log("  MBTI: 未推断")

    _print_and_log("\n━━━ 价值层 Values ━━━")
    _print_and_log("  价值观:")
    for i, v in enumerate(profile.values_layer.values, 1):
        _print_and_log(f"    {i}. {v}")
    _print_and_log("  动机驱动:")
    for i, d in enumerate(profile.values_layer.motivational_drivers, 1):
        _print_and_log(f"    {i}. {d}")

    _print_and_log("\n━━━ 兴趣层 Interest ━━━")
    _print_and_log("  喜好:")
    for dom in profile.interest.likes:
        spec_names = [s.name for s in dom.specifics[:5]]
        specs = ", ".join(spec_names) if spec_names else "无具体项"
        _print_and_log(f"    ▸ {dom.domain} ({dom.weight:.2f})")
        _print_and_log(f"      └ {specs}")
    _print_and_log("  讨厌:")
    for dom in profile.interest.dislikes:
        _print_and_log(f"    ✗ {dom.domain}")
    _print_and_log(f"  常看UP主: {profile.interest.favorite_up_users}")

    _print_and_log("\n━━━ 角色层 Role ━━━")
    _print_and_log(f"  生活阶段: {profile.role.life_stage}")
    _print_and_log(f"  当前状态: {profile.role.current_phase}")

    _print_and_log("\n━━━ 表层 Surface ━━━")
    _print_and_log("  认知风格:")
    for i, s in enumerate(profile.surface.cognitive_style, 1):
        _print_and_log(f"    {i}. {s}")
    _print_and_log(f"  深度偏好: {profile.surface.style.depth_preference}")
    _print_and_log(f"  探索开放度: {profile.surface.exploration_openness}")

    _print_and_log("\n━━━ 综合叙事 ━━━")
    _print_and_log(profile.personality_portrait)

    display_step.save_text("output.txt", "\n".join(output_lines))

    # Finish run
    summary_path = rl.finish(
        data_sources={
            "history": len(history),
            "favorites": len(favorites_data),
            "following": len(following_data),
            "events_total": len(events),
        },
        profile_summary={
            "core_traits": profile.core.core_traits,
            "mbti": profile.core.mbti.type,
            "interest_domains": [d.domain for d in profile.interest.likes],
        },
    )

    print(f"\n{'=' * 60}")
    print(f"数据来源: {len(history)} 浏览 + {len(favorites_data)} 收藏 + {len(following_data)} 关注")
    print(f"完整日志: {rl.run_dir}")
    print(f"摘要: {summary_path}")
    print("=" * 60)

    # --- 5. Human feedback → optimization cycle ---
    from openbiliclaw.eval.human_feedback import (
        collect_human_feedback,
        run_optimization_cycle,
    )

    feedback = collect_human_feedback()
    if feedback is not None:
        result = await run_optimization_cycle(
            feedback,
            project_root=PROJECT_ROOT,
            task="init",
            run_logger=rl,
        )
        if result.get("optimized"):
            print(f"\n优化完成: {result.get('summary', '')[:80]}")
        else:
            print(f"\n未优化: {result.get('reason', '')}")


if __name__ == "__main__":
    asyncio.run(main())
