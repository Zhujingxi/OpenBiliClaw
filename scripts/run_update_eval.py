"""Mode 1: Human evaluation of incremental profile updates.

Takes the current profile + recent events, runs them through the pipeline,
shows per-layer changes, and lets the user evaluate each change.

Usage:
    .venv/bin/python scripts/run_update_eval.py [--events 50]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _diff_layer(label: str, before: Any, after: Any) -> list[str]:
    """Compare two dicts/lists and return human-readable change descriptions."""
    changes: list[str] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in set(list(before.keys()) + list(after.keys())):
            old_val = before.get(key)
            new_val = after.get(key)
            if old_val != new_val:
                changes.append(f"  {key}: {_short(old_val)} → {_short(new_val)}")
    elif isinstance(before, list) and isinstance(after, list):
        if before != after:
            added = [x for x in after if x not in before]
            removed = [x for x in before if x not in after]
            if added:
                changes.append(f"  新增: {_short(added)}")
            if removed:
                changes.append(f"  移除: {_short(removed)}")
    elif before != after:
        changes.append(f"  {_short(before)} → {_short(after)}")
    return changes


def _short(val: Any, limit: int = 80) -> str:
    s = str(val)
    return s[:limit] + "..." if len(s) > limit else s


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=50, help="最近 N 条事件用于增量更新")
    args = parser.parse_args()

    from openbiliclaw.config import load_config
    from openbiliclaw.eval.run_logger import RunLogger
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine
    from openbiliclaw.soul.pipeline import signals_from_events
    from openbiliclaw.soul.profile import OnionProfile
    from _model_runtime import build_script_model_bundle

    cfg = load_config()
    data_dir = cfg.data_path
    memory = MemoryManager(data_dir)
    memory.initialize()

    rl = RunLogger(task="update_human", data_dir=data_dir)

    print("=" * 60)
    print("增量更新画像 — 人工评测 (Mode 1)")
    print(f"日志目录: {rl.run_dir}")
    print("=" * 60)

    # 1. Load current profile (before)
    soul_data = memory.get_layer("soul").data
    if not soul_data:
        print("❌ 尚未初始化画像，请先运行 openbiliclaw init")
        return

    profile_before = OnionProfile.from_dict(soul_data)
    before_dict = profile_before.to_dict()

    before_step = rl.step("before")
    before_step.save_json("profile_before.json", before_dict)

    print("\n[1/4] 当前画像已加载")
    print(f"  核心特质: {profile_before.core.core_traits[:3]}")
    print(f"  兴趣宽域: {[d.domain for d in profile_before.interest.likes[:5]]}")

    # 2. Fetch recent events
    print(f"\n[2/4] 获取最近 {args.events} 条事件...")
    recent_events = memory.query_events(limit=args.events)
    print(f"  获取到 {len(recent_events)} 条事件")
    if recent_events:
        from collections import Counter
        types = Counter(str(e.get("event_type", "")) for e in recent_events)
        print(f"  类型分布: {dict(types.most_common(5))}")
        titles = [str(e.get("title", "")) for e in recent_events[:5] if e.get("title")]
        if titles:
            print("  最近 5 条:")
            for t in titles:
                print(f"    - {t}")

    events_step = rl.step("events")
    events_step.save_json("recent_events.json", recent_events[:100])

    if not recent_events:
        print("  ❌ 无事件数据")
        return

    # 3. Run pipeline update
    print("\n[3/4] 运行 Pipeline 增量更新...")
    model_bundle = build_script_model_bundle(cfg, memory)
    engine = SoulEngine(
        llm=model_bundle.chat_route,
        memory=memory,
        embedding_service=model_bundle.embedding_service,
    )
    pipeline = engine.pipeline

    # Normalize events for pipeline
    norm_events: list[dict[str, Any]] = []
    for ev in recent_events:
        meta = ev.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        norm_events.append({
            "event_type": str(ev.get("event_type", "")),
            "title": str(ev.get("title", "")),
            "metadata": meta,
        })

    signals = signals_from_events(norm_events)
    result = await pipeline.ingest_batch(signals)
    flush_result = await pipeline.flush()

    print(f"  信号数: {result.signals_accepted}")
    print(f"  缓冲到: {result.layers_buffered}")
    layers_updated = result.layers_updated + flush_result.layers_updated
    print(f"  更新了 {len(layers_updated)} 层")
    for lr in layers_updated:
        print(f"    {lr.layer.value}: changed={lr.changed}, signals={lr.signals_consumed}")
        for c in lr.changes:
            print(f"      - {c}")

    # 4. Show per-layer diff for human evaluation
    profile_after = OnionProfile.from_dict(memory.get_layer("soul").data)
    after_dict = profile_after.to_dict()

    after_step = rl.step("after")
    after_step.save_json("profile_after.json", after_dict)
    after_step.save_json("pipeline_result.json", {
        "signals_accepted": result.signals_accepted,
        "layers_buffered": result.layers_buffered,
        "layers_updated": [
            {"layer": lr.layer.value, "changed": lr.changed,
             "changes": lr.changes, "signals_consumed": lr.signals_consumed}
            for lr in layers_updated
        ],
    })

    print("\n[4/4] 逐层变化对比 — 请评测")
    print("=" * 60)

    layer_names = [
        ("core", "核心层 Core"),
        ("values_layer", "价值层 Values"),
        ("interest", "兴趣层 Interest"),
        ("role", "角色层 Role"),
        ("surface", "表层 Surface"),
    ]

    any_change = False
    for key, label in layer_names:
        old = before_dict.get(key, {})
        new = after_dict.get(key, {})
        if old == new:
            print(f"\n━━━ {label} ━━━  (无变化)")
            continue
        any_change = True
        print(f"\n━━━ {label} ━━━  ⚡ 有变化")
        changes = _diff_layer(label, old, new)
        for c in changes:
            print(c)

    # Portrait diff
    old_portrait = str(before_dict.get("personality_portrait", ""))[:100]
    new_portrait = str(after_dict.get("personality_portrait", ""))[:100]
    if old_portrait != new_portrait:
        any_change = True
        print("\n━━━ 综合叙事 ━━━  ⚡ 有变化")
        print(f"  旧: {old_portrait}...")
        print(f"  新: {new_portrait}...")

    if not any_change:
        print("\n  画像无变化（事件未达到任何层的更新阈值）")

    rl.finish(
        events_count=len(recent_events),
        any_change=any_change,
        layers_updated=[lr.layer.value for lr in layers_updated if lr.changed],
    )

    print(f"\n{'=' * 60}")
    print(f"完整日志: {rl.run_dir}")
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
            task="incremental_update",
            run_logger=rl,
        )
        if result.get("optimized"):
            print(f"\n优化完成: {result.get('summary', '')[:80]}")
        else:
            print(f"\n未优化: {result.get('reason', '')}")


if __name__ == "__main__":
    asyncio.run(main())
