"""Mode 2: Automated SGD/RL optimization for incremental pipeline updates.

Loop: generate persona → init baseline profile → simulate incremental events
→ pipeline update → evaluate per-layer changes → optimize prompts/thresholds.

Changes are actually applied between epochs using PromptOptimizer,
with commit/rollback based on score improvement.

Usage:
    .venv/bin/python scripts/run_update_auto_optimize.py [--rounds 3] [--batch 2]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("eval.update_optimize")


async def run_update_pipeline(
    *,
    init_events: list[dict[str, Any]],
    update_events: list[dict[str, Any]],
) -> Any:
    """Run init + incremental update in an isolated temp environment."""
    from openbiliclaw.config import load_config
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine
    from openbiliclaw.soul.pipeline import signals_from_events
    from openbiliclaw.soul.profile import OnionProfile
    from _model_runtime import build_script_model_bundle

    cfg = load_config()

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        memory = MemoryManager(data_dir)
        memory.initialize()

        model_bundle = build_script_model_bundle(cfg, memory)
        engine = SoulEngine(
            llm=model_bundle.chat_route,
            memory=memory,
            embedding_service=model_bundle.embedding_service,
        )

        # Phase 1: Init baseline profile from init_events
        history = [
            {
                "title": str(e.get("title", "")),
                "history": {"bvid": str((e.get("metadata") or {}).get("bvid", ""))},
                "author_name": str(
                    e.get("up_name", "")
                    or (e.get("metadata") or {}).get("up_name", "")
                ),
            }
            for e in init_events
        ]
        await engine.analyze_events(init_events)
        await engine.build_initial_profile(history)

        # Phase 2: Incremental update from update_events
        pipeline = engine.pipeline
        signals = signals_from_events(update_events)
        await pipeline.ingest_batch(signals)
        await pipeline.flush()

        # Return final profile
        soul_data = memory.get_layer("soul").data
        return OnionProfile.from_dict(soul_data) if soul_data else OnionProfile()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--explore-rate", type=float, default=0.2)
    parser.add_argument(
        "--reuse-personas", action="store_true",
        help="Reuse cached personas from pool instead of generating new ones",
    )
    args = parser.parse_args()

    from claude_agent_sdk import ClaudeAgentOptions

    from openbiliclaw.eval.agents import (
        ONION_PROFILE_SCHEMA,
        PERSONA_SCHEMA_HINT,
        collect_json,
        run_optimizer_agent,
    )
    from openbiliclaw.eval.evaluator import FIELD_TO_PIPELINE, ProfileEvaluator
    from openbiliclaw.eval.optimizer import MODIFIABLE_FILES, ParamChange, PromptOptimizer
    from openbiliclaw.eval.persona_pool import PersonaPool
    from openbiliclaw.eval.report import render_training_summary
    from openbiliclaw.eval.run_logger import RunLogger, RunStep
    from openbiliclaw.soul.profile import OnionProfile

    rl = RunLogger(task="auto_update", data_dir=Path("data"))
    rl.setup_file_logging()
    persona_pool = PersonaPool()
    logger.info("Persona pool: %d cached (update task)", persona_pool.count("update"))

    optimizer = PromptOptimizer(project_root=PROJECT_ROOT)
    evaluator = ProfileEvaluator()
    best_score = 0.0
    patience = 0
    history_log: list[dict[str, Any]] = []

    # Persona pool for diversity
    personas_pool = [
        {"mbti": "INTJ", "depth": "hardcore", "interest_breadth": "specialist",
         "shift": "new_interest", "shift_desc": "用户开始关注一个全新领域"},
        {"mbti": "ENFP", "depth": "casual", "interest_breadth": "generalist",
         "shift": "deepen", "shift_desc": "用户对已有兴趣大幅加深"},
        {"mbti": "ISTP", "depth": "moderate", "interest_breadth": "specialist",
         "shift": "abandon", "shift_desc": "用户放弃了一个之前很喜欢的领域"},
        {"mbti": "INFJ", "depth": "hardcore", "interest_breadth": "generalist",
         "shift": "life_change", "shift_desc": "用户生活阶段发生了变化"},
    ]

    logger.info("=" * 60)
    logger.info("自动优化循环 — 增量更新 (Pipeline) 任务")
    logger.info("轮次: %d, 每轮 batch: %d", args.rounds, args.batch)
    logger.info("=" * 60)

    for epoch in range(1, args.rounds + 1):
        logger.info("━" * 60)
        logger.info("Epoch %d/%d", epoch, args.rounds)
        logger.info("━" * 60)

        batch_constraints = random.sample(
            personas_pool, min(args.batch, len(personas_pool)),
        )

        train_reports = []

        for i, constraints in enumerate(batch_constraints, 1):
            shift = constraints.get("shift", "new_interest")
            shift_desc = constraints.get("shift_desc", "")
            logger.info("[%d.%d] Persona: %s %s", epoch, i, constraints["mbti"], constraints["depth"])
            logger.info("  兴趣变化类型: %s — %s", shift, shift_desc)

            # 1. Generate or reuse ground truth: initial + evolved persona
            data: dict[str, Any] | None = None

            if args.reuse_personas:
                data = persona_pool.load_matching("update", constraints)
                if data:
                    logger.info("  ♻️ 复用缓存 persona")

            if data is None:
                logger.info("  → 生成 ground truth (分两步：初始 + 演变)...")
                constraint_text = json.dumps(
                    {k: v for k, v in constraints.items() if k not in ("shift", "shift_desc")},
                    ensure_ascii=False,
                )
                try:
                    # Step 1: generate initial profile
                    initial_data = await collect_json(
                        label=f"persona_{epoch}.{i}_initial",
                        prompt=(
                            f"生成一个虚构的 B站 用户画像。\n"
                            f"约束: {constraint_text}\n\n"
                            f"请返回 JSON 代码块，personality_portrait 至少 200 字，"
                            f"likes 至少 3 个 domain 每个至少 2 个 specific。\n\n"
                            f"{PERSONA_SCHEMA_HINT}"
                        ),
                        options=ClaudeAgentOptions(
                            system_prompt="你是用户画像生成器。直接返回 ```json 代码块，不要有任何前置解释或后置说明。",
                            max_turns=1,
                        ),
                        max_retries=2,
                        json_schema=ONION_PROFILE_SCHEMA,
                    )
                    # Step 2: generate evolved profile based on initial + shift
                    initial_summary = json.dumps(initial_data, ensure_ascii=False)[:1500]
                    evolved_data = await collect_json(
                        label=f"persona_{epoch}.{i}_evolved",
                        prompt=(
                            f"基于以下初始用户画像，生成经历「{shift_desc}」变化后的演变画像。\n\n"
                            f"初始画像:\n{initial_summary}\n\n"
                            f"要求：\n"
                            f"- 核心层（core_traits, deep_needs, mbti）基本稳定\n"
                            f"- 兴趣层体现「{shift_desc}」的变化\n"
                            f"- 返回完整的演变后画像 JSON（不是 diff）\n"
                            f"- 额外在顶层加一个 change_summary 字段描述变化\n\n"
                            f"{PERSONA_SCHEMA_HINT}\n"
                            f'顶层额外字段: "change_summary": "描述从初始到演变的变化"'
                        ),
                        options=ClaudeAgentOptions(
                            system_prompt="你是用户画像演变模拟器。直接返回 ```json 代码块，不要有任何前置解释或后置说明。",
                            max_turns=1,
                        ),
                        max_retries=2,
                        json_schema=ONION_PROFILE_SCHEMA,
                    )
                    change_summary = str(evolved_data.pop("change_summary", ""))
                    data = {
                        "initial": initial_data,
                        "evolved": evolved_data,
                        "change_summary": change_summary,
                    }
                    persona_pool.save("update", constraints, data)
                except Exception as exc:
                    logger.warning("  ⚠️ 生成失败 (%s)，尝试从缓存加载...", exc)
                    data = persona_pool.load_matching("update", constraints)
                    if data is None:
                        logger.error("  ❌ 缓存中也没有匹配的 persona，跳过")
                        continue
                    logger.info("  ♻️ 使用缓存 persona 替代")

            try:
                initial_profile = OnionProfile.from_dict(data.get("initial", {}))
                evolved_profile = OnionProfile.from_dict(data.get("evolved", {}))
                change_summary = str(data.get("change_summary", ""))
                logger.info("  ✅ 初始: %s", initial_profile.core.core_traits[:2])
                logger.info("  ✅ 演变: %s", evolved_profile.core.core_traits[:2])
                logger.info("  变化: %s", change_summary[:60])
            except Exception as exc:
                logger.error("  ❌ Persona 解析失败: %s", exc)
                continue

            ps = rl.persona_dir(epoch, i)
            ps.save_json("initial_gt.json", data.get("initial", {}))
            ps.save_json("evolved_gt.json", data.get("evolved", {}))
            ps.save_text("change_summary.txt", change_summary)

            # 2. Generate init events + update events (with retry)
            logger.info("  → 生成初始事件 + 增量事件...")
            try:
                events_data = await collect_json(
                    label=f"events_{epoch}.{i}",
                    prompt=(
                        f"生成两组 B站 行为事件：\n"
                        f"1. init_events: 30 条，对应初始画像的行为\n"
                        f"2. update_events: 20 条，对应演变后新增的行为（体现 {shift_desc}）\n\n"
                        f"## 必须包含的要素\n"
                        f"1. **每条事件必须有 up_name**（UP主频道名），从画像的兴趣领域推断合理的真实 UP 主名\n"
                        f"2. **每组至少 3 条负面事件**（event_type 为 skip 或 dislike），体现用户不喜欢的内容\n"
                        f"3. skip 事件需包含 completion_rate（0.0-0.3 表示快速跳过）\n"
                        f"4. 视频标题要真实、具体\n\n"
                        f"## 事件类型\n"
                        f"view / search / like / favorite / skip / dislike\n\n"
                        f"初始画像:\n{initial_profile.to_llm_context()[:500]}\n\n"
                        f"演变画像:\n{evolved_profile.to_llm_context()[:500]}\n\n"
                        f"## 输出格式\n"
                        f'```json\n{{"init_events": [{{"event_type": "view", "title": "...", "up_name": "..."}}, '
                        f'{{"event_type": "skip", "title": "...", "up_name": "...", "completion_rate": 0.15}}], '
                        f'"update_events": [...]}}\n```'
                    ),
                    options=ClaudeAgentOptions(
                        system_prompt="你是行为模拟器。直接返回 ```json 代码块，不要有任何前置解释。",
                        max_turns=1,
                    ),
                    max_retries=2,
                )
                init_events = [e for e in events_data.get("init_events", []) if isinstance(e, dict)]
                update_events = [e for e in events_data.get("update_events", []) if isinstance(e, dict)]
                logger.info("  ✅ 初始事件: %d, 增量事件: %d", len(init_events), len(update_events))
                ps.save_json("init_events.json", init_events)
                ps.save_json("update_events.json", update_events)
            except Exception as exc:
                logger.error("  ❌ 事件生成失败: %s", exc)
                continue

            if len(init_events) < 5 or len(update_events) < 3:
                logger.warning("  ⚠️ 事件太少，跳过")
                continue

            # 3. Run init + pipeline update
            logger.info("  → 运行 init + pipeline 增量更新...")
            try:
                predicted = await run_update_pipeline(
                    init_events=init_events,
                    update_events=update_events,
                )
                logger.info("  ✅ 最终画像: %s", predicted.core.core_traits[:2])
            except Exception as exc:
                logger.error("  ❌ Pipeline 失败: %s", exc)
                continue

            ps.save_json("predicted.json", predicted.to_dict())

            # 4. Evaluate against evolved profile (ground truth)
            logger.info("  → 评估（预测 vs 演变后的 ground truth）...")
            report = await evaluator.evaluate(evolved_profile, predicted)
            train_reports.append(report)
            ps.save_json("eval_report.json", report.to_dict())
            logger.info("  📊 Score: %.3f", report.overall_score)
            for ls in report.layer_scores:
                icon = "✅" if ls.score >= 0.8 else "⚠️" if ls.score >= 0.5 else "❌"
                logger.info("    %s %s: %.3f", icon, ls.layer, ls.score)

        if not train_reports:
            logger.warning("  ❌ 本轮无有效评估，跳过")
            continue

        # 5. Aggregate and optimize
        train_mean = sum(r.overall_score for r in train_reports) / len(train_reports)
        logger.info("📈 Epoch %d train mean: %.3f", epoch, train_mean)

        worst_map: dict[str, Any] = {}
        for report in train_reports:
            for f in report.worst_fields:
                key = f"{f.layer}.{f.field}"
                if key not in worst_map or f.score < worst_map[key].score:
                    worst_map[key] = f
        worst_fields = sorted(worst_map.values(), key=lambda f: f.score)[:5]

        logger.info("最大偏差:")
        for f in worst_fields[:3]:
            logger.info("  %s.%s: %.2f — %s", f.layer, f.field, f.score, f.deviation[:50])

        # Epoch 1 is baseline-only: record score without optimizing
        if epoch == 1:
            best_score = train_mean
            epoch_result = {
                "epoch": epoch,
                "train_mean": round(train_mean, 4),
                "action": "BASELINE",
                "changes_applied": 0,
                "summary": "Baseline evaluation (no optimization)",
                "worst": [
                    {"field": f"{f.layer}.{f.field}", "score": f.score}
                    for f in worst_fields[:3]
                ],
                "accepted": True,
            }
            logger.info("📊 BASELINE — score: %.3f (will optimize from epoch 2)", best_score)
            history_log.append(epoch_result)
            continue

        is_explore = random.random() < args.explore_rate
        action = "EXPLORE" if is_explore else "EXPLOIT"
        logger.info("策略: %s", action)

        # Optimizer — include pipeline-specific context (epoch >= 2)
        logger.info("→ 运行 Optimizer Agent...")
        combined_report = {
            "task": "incremental_update",
            "train_mean": train_mean,
            "worst_fields": [
                {"layer": f.layer, "field": f.field, "score": f.score,
                 "deviation": f.deviation}
                for f in worst_fields
            ],
            "action": action,
            "note": "这是增量更新任务的优化。关注 pipeline 的分层更新逻辑、"
                    "layer_updaters.py 的 update_surface/update_interest，"
                    "以及 prompts.py 中 build_preference_analysis_prompt 的质量。",
            "pipeline_hints": {
                f.layer + "." + f.field: FIELD_TO_PIPELINE.get(f"{f.layer}.{f.field}", "")
                for f in worst_fields
                if FIELD_TO_PIPELINE.get(f"{f.layer}.{f.field}")
            },
            "modifiable_files": MODIFIABLE_FILES,
        }
        optimization = await run_optimizer_agent(combined_report, PROJECT_ROOT)
        raw_changes = optimization.get("changes", [])
        summary = optimization.get("summary", "无建议")

        epoch_d = rl.epoch_dir(epoch)
        opt_step = RunStep(epoch_d / "optimizer", "optimizer")
        opt_step.save_json("eval_input.json", combined_report)
        opt_step.save_json("optimizer_output.json", optimization)

        logger.info("建议: %s", summary[:80])
        logger.info("修改数: %d", len(raw_changes))

        # Convert raw changes to ParamChange and APPLY
        param_changes = [
            ParamChange(
                param_name=str(c.get("file_path", "")),
                change_type="prompt",
                old_value=str(c.get("old_text", "")),
                new_value=str(c.get("new_text", "")),
                description=str(c.get("reason", "")),
                file_path=str(c.get("file_path", "")),
            )
            for c in raw_changes
            if isinstance(c, dict) and c.get("old_text") and c.get("new_text")
        ]

        applied_count = 0
        if param_changes:
            applied_count = optimizer.apply(param_changes)
            logger.info("📝 提出 %d 处修改，成功应用 %d 处", len(param_changes), applied_count)
            if applied_count < len(param_changes):
                logger.warning("⚠️ %d 处修改未匹配", len(param_changes) - applied_count)
            for pc in param_changes:
                logger.info("  %s: %s", pc.file_path, pc.description[:60])

            # Pytest gate for pipeline code changes
            if applied_count > 0 and optimizer.has_pipeline_changes():
                logger.info("🧪 检测到 pipeline 代码修改，运行 pytest...")
                passed, test_output = optimizer.validate_with_tests()
                if not passed:
                    optimizer.rollback()
                    logger.error("❌ 测试失败，已回滚: %s", test_output[:100])
                    applied_count = 0
                else:
                    logger.info("✅ 测试通过")

            for pc in param_changes:
                old_preview = str(pc.old_value)[:50]
                new_preview = str(pc.new_value)[:50]
                logger.info("  %s: %s... → %s...", pc.file_path, old_preview, new_preview)

        # Accept/rollback
        epoch_result = {
            "epoch": epoch,
            "train_mean": round(train_mean, 4),
            "action": action,
            "changes_applied": applied_count,
            "summary": summary,
            "worst": [
                {"field": f"{f.layer}.{f.field}", "score": f.score}
                for f in worst_fields[:3]
            ],
        }

        if train_mean > best_score:
            best_score = train_mean
            patience = 0
            epoch_result["accepted"] = True
            if applied_count > 0:
                optimizer.commit()
                logger.info("✅ ACCEPT + COMMIT (%d 处) — 新最佳: %.3f", applied_count, best_score)
            else:
                logger.info("✅ ACCEPT (无有效修改) — 新最佳: %.3f", best_score)
        else:
            patience += 1
            epoch_result["accepted"] = False
            if applied_count > 0:
                optimizer.rollback()
                logger.info("↩️ ROLLBACK (%d 处) — (%.3f <= %.3f), patience=%d/3", applied_count, train_mean, best_score, patience)
            else:
                logger.info("↩️ 未超越最佳 (%.3f <= %.3f), patience=%d/3", train_mean, best_score, patience)

        history_log.append(epoch_result)

        if patience >= 3:
            logger.info("⛔ Early stopping")
            break

    # Summary
    logger.info("=" * 60)
    logger.info("优化完成")
    logger.info("=" * 60)

    result = {
        "task": "incremental_update",
        "epochs_run": len(history_log),
        "best_score": best_score,
        "best_epoch": max(
            (h for h in history_log if h.get("accepted")),
            key=lambda h: h["train_mean"],
            default={"epoch": 0},
        ).get("epoch", 0),
        "stop_reason": "early_stop" if patience >= 3 else "max_epochs",
        "history": history_log,
    }

    logger.info(render_training_summary(result))

    rl.finish(best_score=best_score, epochs_run=len(history_log))
    logger.info("完整日志: %s", rl.run_dir)

    report_dir = Path("data/eval/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = report_dir / f"auto_update_optimize_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("报告: %s", path)


if __name__ == "__main__":
    asyncio.run(main())
