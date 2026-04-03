"""Run automated self-optimization loop for init profile generation.

SGD/RL loop: generate persona → simulate events → init pipeline →
evaluate → optimize prompts → apply changes → validate → accept/rollback.

Changes are *actually applied* between epochs using PromptOptimizer,
with commit/rollback based on score improvement. Original files are
restored at the end, with the best diff saved for manual review.

Usage:
    .venv/bin/python scripts/run_auto_optimize.py [--rounds 3] [--batch 2]
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

logger = logging.getLogger("eval.init_optimize")


async def run_init_pipeline(
    events: list[dict[str, Any]],
) -> Any:
    """Run init profile generation in an isolated temp environment."""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.registry import build_llm_registry
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine

    cfg = load_config()

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        memory = MemoryManager(data_dir)
        memory.initialize()

        registry = build_llm_registry(cfg)
        engine = SoulEngine(llm=registry, memory=memory)

        # Convert to history format for init
        history = [
            {
                "title": str(e.get("title", "")),
                "history": {"bvid": str((e.get("metadata") or {}).get("bvid", ""))},
                "author_name": str(
                    e.get("up_name", "")
                    or (e.get("metadata") or {}).get("up_name", "")
                ),
            }
            for e in events
        ]

        await engine.analyze_events(events)
        profile = await engine.build_initial_profile(history)
        return profile


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

    rl = RunLogger(task="auto_init", data_dir=Path("data"))
    rl.setup_file_logging()
    persona_pool = PersonaPool()
    logger.info("Persona pool: %d cached (init task)", persona_pool.count("init"))

    optimizer = PromptOptimizer(project_root=PROJECT_ROOT)
    evaluator = ProfileEvaluator()
    best_score = 0.0
    patience = 0
    history_log: list[dict[str, Any]] = []

    # Persona diversity dimensions
    personas_pool = [
        {"mbti": "INTJ", "depth": "hardcore", "interest_breadth": "specialist"},
        {"mbti": "ENFP", "depth": "casual", "interest_breadth": "generalist"},
        {"mbti": "ISTP", "depth": "moderate", "interest_breadth": "specialist"},
        {"mbti": "ESFJ", "depth": "casual", "interest_breadth": "generalist"},
        {"mbti": "INFJ", "depth": "hardcore", "interest_breadth": "generalist"},
        {"mbti": "ENTP", "depth": "moderate", "interest_breadth": "specialist"},
    ]

    logger.info("=" * 60)
    logger.info("自动优化循环 — init 画像任务")
    logger.info("轮次: %d, 每轮 batch: %d, 探索率: %.1f", args.rounds, args.batch, args.explore_rate)
    logger.info("=" * 60)

    for epoch in range(1, args.rounds + 1):
        logger.info("━" * 60)
        logger.info("Epoch %d/%d", epoch, args.rounds)
        logger.info("━" * 60)

        # 1. Sample mini-batch of personas
        batch_constraints = random.sample(
            personas_pool, min(args.batch, len(personas_pool)),
        )

        train_reports = []

        for i, constraints in enumerate(batch_constraints, 1):
            logger.info("[%d.%d] Persona: %s", epoch, i, constraints)

            # 1a. Generate or reuse ground truth persona
            gt_data: dict[str, Any] | None = None

            if args.reuse_personas:
                gt_data = persona_pool.load_matching("init", constraints)
                if gt_data:
                    logger.info("  ♻️ 复用缓存 persona")

            if gt_data is None:
                logger.info("  → 生成 ground truth persona...")
                try:
                    gt_data = await collect_json(
                        prompt=(
                            f"生成一个虚构的 B站 用户画像，约束条件：{json.dumps(constraints, ensure_ascii=False)}\n"
                            f"请返回 JSON 代码块，personality_portrait 至少 200 字，likes 至少 3 个 domain 每个至少 2 个 specific。\n\n"
                            f"{PERSONA_SCHEMA_HINT}"
                        ),
                        options=ClaudeAgentOptions(
                            system_prompt="你是用户画像生成器。直接返回 ```json 代码块，不要有任何前置解释或后置说明。第一个字符必须是 ```。",
                            max_turns=1,
                        ),
                        max_retries=2,
                        json_schema=ONION_PROFILE_SCHEMA,
                    )
                    persona_pool.save("init", constraints, gt_data)
                except Exception as exc:
                    logger.warning("  ⚠️ 生成失败 (%s)，尝试从缓存加载...", exc)
                    gt_data = persona_pool.load_matching("init", constraints)
                    if gt_data is None:
                        logger.error("  ❌ 缓存中也没有匹配的 persona，跳过")
                        continue
                    logger.info("  ♻️ 使用缓存 persona 替代")

            try:
                ground_truth = OnionProfile.from_dict(gt_data)
                logger.info("  ✅ GT: %s", ground_truth.core.core_traits[:3])
            except Exception as exc:
                logger.error("  ❌ Persona 解析失败: %s", exc)
                continue

            # Log persona
            ps = rl.persona_dir(epoch, i)
            ps.save_json("ground_truth.json", gt_data)

            # 1b. Generate simulated events (with retry)
            logger.info("  → 生成模拟事件...")
            persona_ctx = ground_truth.to_llm_context()
            # Extract ground truth UP主 for event generation grounding
            gt_up_users = ground_truth.interest.favorite_up_users or []
            gt_up_hint = ""
            if gt_up_users:
                gt_up_hint = (
                    f"\n5. **画像中的常看UP主必须出现在事件中**: {', '.join(gt_up_users)}\n"
                    f"   这些 UP主 的视频至少各出现 2 次（作为 up_name），确保偏好分析能提取到\n"
                )
            try:
                events_data = await collect_json(
                    prompt=(
                        f"根据以下用户画像生成 50 条 B站 行为事件。\n\n"
                        f"## 事件类型\n"
                        f"view / search / like / favorite / skip / dislike\n\n"
                        f"## 必须包含的要素\n"
                        f"1. **每条事件必须有 up_name**（UP主频道名），从画像的兴趣领域推断合理的真实 UP 主名\n"
                        f"2. **至少 5 条负面事件**（skip / dislike），体现用户明确不喜欢的内容方向\n"
                        f"3. skip 事件需包含 completion_rate（0.0-0.3 表示快速跳过）\n"
                        f"4. 视频标题要真实、具体\n"
                        f"{gt_up_hint}\n"
                        f"## 输出格式\n"
                        f'```json\n{{"events": [{{"event_type": "view", "title": "...", "up_name": "..."}}, '
                        f'{{"event_type": "skip", "title": "...", "up_name": "...", "completion_rate": 0.1}}]}}\n```\n\n'
                        f"画像:\n{persona_ctx}"
                    ),
                    options=ClaudeAgentOptions(
                        system_prompt="你是行为模拟器。直接返回 ```json 代码块，不要有任何前置解释。",
                        max_turns=1,
                    ),
                    max_retries=2,
                )
                events = events_data.get("events", [])
                events = [e for e in events if isinstance(e, dict) and e.get("event_type")]
                logger.info("  ✅ 生成 %d 条事件", len(events))
                ps.save_json("simulated_events.json", events)
            except Exception as exc:
                logger.error("  ❌ 事件生成失败: %s", exc)
                continue

            if len(events) < 10:
                logger.warning("  ⚠️ 事件太少，跳过")
                continue

            # 1c. Run init pipeline on simulated events
            logger.info("  → 运行 init pipeline...")
            try:
                predicted = await run_init_pipeline(events)
                logger.info("  ✅ 预测画像: %s", predicted.core.core_traits[:3])
            except Exception as exc:
                logger.error("  ❌ Pipeline 失败: %s", exc)
                continue

            # Log predicted profile
            ps.save_json("predicted.json", predicted.to_dict())

            # 1d. Evaluate
            logger.info("  → 评估...")
            report = await evaluator.evaluate(ground_truth, predicted)
            train_reports.append(report)
            ps.save_json("eval_report.json", report.to_dict())
            logger.info("  📊 Score: %.3f", report.overall_score)
            for ls in report.layer_scores:
                icon = "✅" if ls.score >= 0.8 else "⚠️" if ls.score >= 0.5 else "❌"
                logger.info("    %s %s: %.3f", icon, ls.layer, ls.score)

        if not train_reports:
            logger.warning("  ❌ 本轮无有效评估，跳过")
            continue

        # 2. Compute train mean
        train_mean = sum(r.overall_score for r in train_reports) / len(train_reports)
        logger.info("📈 Epoch %d train mean: %.3f", epoch, train_mean)

        # 3. Collect worst fields across batch
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

        # 4. Decide: exploit or explore
        is_explore = random.random() < args.explore_rate
        action = "EXPLORE" if is_explore else "EXPLOIT"
        logger.info("策略: %s", action)

        # 5. Run optimizer
        logger.info("→ 运行 Optimizer Agent...")
        combined_report = {
            "train_mean": train_mean,
            "worst_fields": [
                {"layer": f.layer, "field": f.field, "score": f.score,
                 "deviation": f.deviation}
                for f in worst_fields
            ],
            "action": action,
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

        # Log optimizer output
        epoch_dir = rl.epoch_dir(epoch)
        opt_step = RunStep(epoch_dir / "optimizer", "optimizer")
        opt_step.save_json("eval_input.json", combined_report)
        opt_step.save_json("optimizer_output.json", optimization)

        logger.info("建议: %s", summary[:80])
        logger.info("修改数: %d", len(raw_changes))

        # 6. Convert raw changes to ParamChange and APPLY
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

        # 7. Accept/rollback logic
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

        # Early stopping
        if patience >= 3:
            logger.info("⛔ Early stopping: 连续 3 轮未提升")
            break

    # Final summary
    logger.info("=" * 60)
    logger.info("优化完成")
    logger.info("=" * 60)

    result = {
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

    # Save results
    report_dir = Path("data/eval/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    report_path = report_dir / f"auto_optimize_{ts}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("报告已保存: %s", report_path)


if __name__ == "__main__":
    asyncio.run(main())
