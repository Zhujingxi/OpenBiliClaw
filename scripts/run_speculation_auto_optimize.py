"""Mode 2: Automated SGD/RL optimization for speculative interest generation.

Loop: generate persona -> run speculator -> persona judges speculations ->
simulate future events -> observe/promote/expire -> evaluate -> optimize prompt
-> accept/rollback.

Usage:
    .venv/bin/python scripts/run_speculation_auto_optimize.py [--rounds 3] [--batch 2]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("eval.speculation_optimize")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--explore-rate", type=float, default=0.2)
    parser.add_argument(
        "--reuse-personas", action="store_true",
        help="Reuse cached personas from pool instead of generating new ones",
    )
    parser.add_argument(
        "--skip-persona-judge", action="store_true",
        help="Skip persona judge step (faster but less accurate)",
    )
    args = parser.parse_args()

    from claude_agent_sdk import ClaudeAgentOptions

    from openbiliclaw.eval.agents import (
        ONION_PROFILE_SCHEMA,
        PERSONA_SCHEMA_HINT,
        collect_json,
        run_optimizer_agent,
        run_speculation_event_agent,
    )
    from openbiliclaw.eval.optimizer import MODIFIABLE_FILES, ParamChange, PromptOptimizer
    from openbiliclaw.eval.persona_judge import judge_speculations
    from openbiliclaw.eval.report import render_speculation_training_summary
    from openbiliclaw.eval.run_logger import RunLogger, RunStep
    from openbiliclaw.eval.speculation_evaluator import SpeculationEvaluator
    from openbiliclaw.llm.prompts import build_speculation_generation_prompt
    from openbiliclaw.soul.profile import OnionProfile
    from openbiliclaw.soul.speculator import (
        SpeculativeInterest,
        SpeculativeState,
        expire_stale,
        observe_events,
        promote_ready,
    )

    from openbiliclaw.eval.persona_pool import PersonaPool

    rl = RunLogger(task="speculation_auto", data_dir=Path("data"))
    rl.setup_file_logging()
    persona_pool = PersonaPool()
    logger.info("Persona pool: %d cached (init task)", persona_pool.count("init"))

    optimizer = PromptOptimizer(project_root=PROJECT_ROOT)
    evaluator = SpeculationEvaluator()

    best_score = 0.0
    patience = 0
    history_log: list[dict[str, Any]] = []

    personas_pool = [
        {"mbti": "INTJ", "depth": "hardcore", "interest_breadth": "specialist"},
        {"mbti": "ENFP", "depth": "casual", "interest_breadth": "generalist"},
        {"mbti": "ISTP", "depth": "moderate", "interest_breadth": "specialist"},
        {"mbti": "INFJ", "depth": "hardcore", "interest_breadth": "generalist"},
        {"mbti": "ENTP", "depth": "moderate", "interest_breadth": "generalist"},
        {"mbti": "ESFJ", "depth": "casual", "interest_breadth": "specialist"},
    ]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("=" * 60)
    logger.info("自动优化循环 — 推测兴趣生成")
    logger.info("轮次: %d, 每轮 batch: %d, persona_judge: %s",
                args.rounds, args.batch, "OFF" if args.skip_persona_judge else "ON")
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
            logger.info("[%d.%d] Persona: %s", epoch, i, constraints)

            # 1. Generate or reuse ground truth persona
            gt_data: dict[str, Any] | None = None

            if args.reuse_personas:
                gt_data = persona_pool.load_matching("init", constraints)
                if gt_data:
                    logger.info("  Reusing cached persona")

            if gt_data is None:
                logger.info("  Generating persona...")
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
                        label=f"persona_{epoch}_{i}",
                    )
                    persona_pool.save("init", constraints, gt_data)
                except Exception as exc:
                    logger.warning("  Generation failed (%s), loading from cache...", exc)
                    gt_data = persona_pool.load_matching("init", constraints)
                    if gt_data is None:
                        logger.error("  No cached persona available, skipping")
                        continue
                    logger.info("  Using cached persona")

            try:
                profile = OnionProfile.from_dict(gt_data)
                logger.info("  Profile: %s", profile.core.core_traits[:2])
            except Exception as exc:
                logger.error("  Persona parse failed: %s", exc)
                continue

            ps = rl.persona_dir(epoch, i)
            ps.save_json("profile.json", gt_data)

            # 2. Run speculation generation
            logger.info("  Generating speculations...")
            confirmed_domains = [d.domain for d in profile.interest.likes]
            profile_ctx = profile.to_llm_context()

            try:
                spec_data = await collect_json(
                    prompt=(
                        build_speculation_generation_prompt(
                            profile_summary=profile_ctx,
                            existing_speculations=[],
                            cooldown_domains=[],
                            confirmed_domains=confirmed_domains,
                            count=5,
                        )[1]["content"]  # user prompt
                    ),
                    options=ClaudeAgentOptions(
                        system_prompt=build_speculation_generation_prompt(
                            profile_summary="",
                            existing_speculations=[],
                            cooldown_domains=[],
                            confirmed_domains=[],
                        )[0]["content"],  # system prompt
                        max_turns=1,
                    ),
                    max_retries=2,
                    label=f"spec_gen_{epoch}_{i}",
                )
                raw_specs = spec_data.get("speculations", [])
                now = datetime.now()
                speculations = [
                    SpeculativeInterest(
                        domain=str(s.get("domain", "")),
                        category=str(s.get("category", "")),
                        reason=str(s.get("reason", "")),
                        confidence=float(s.get("confidence", 0.4)),
                        created_at=now.isoformat(),
                        status="active",
                    )
                    for s in raw_specs
                    if isinstance(s, dict) and s.get("domain")
                ]
                logger.info("  Generated %d speculations", len(speculations))
                for spec in speculations:
                    logger.info("    [%s] %s", spec.category, spec.domain)
                ps.save_json("speculations.json", [s.to_dict() for s in speculations])
            except Exception as exc:
                logger.error("  Speculation generation failed: %s", exc)
                continue

            if not speculations:
                logger.warning("  No speculations, skipping")
                continue

            # 3. Persona judges speculations (virtual persona role-play)
            persona_judgment = None
            if not args.skip_persona_judge:
                logger.info("  Persona judging speculations...")
                try:
                    persona_judgment = await judge_speculations(
                        persona_context=profile_ctx,
                        speculations=[
                            {"domain": s.domain, "reason": s.reason}
                            for s in speculations
                        ],
                        max_retries=2,
                    )
                    logger.info("  Persona resonance: %.2f", persona_judgment.mean_resonance)
                    for v in persona_judgment.verdicts:
                        click = "Y" if v.would_click else "N"
                        logger.info(
                            "    [%s] %.2f %s — %s",
                            click, v.resonance_score, v.domain, v.reasoning[:50],
                        )
                    ps.save_json("persona_judgment.json", {
                        "mean_resonance": persona_judgment.mean_resonance,
                        "verdicts": [
                            {
                                "domain": v.domain,
                                "would_click": v.would_click,
                                "resonance_score": v.resonance_score,
                                "reasoning": v.reasoning,
                            }
                            for v in persona_judgment.verdicts
                        ],
                    })
                except Exception as exc:
                    logger.warning("  Persona judge failed: %s", exc)

            # 4. Generate simulated future events
            logger.info("  Generating simulated events...")
            try:
                events_data = await run_speculation_event_agent(
                    speculations=[
                        {"domain": s.domain, "category": s.category}
                        for s in speculations
                    ],
                    event_count=30,
                    matching_ratio=0.4,
                )
                matching = [e for e in events_data.get("matching_events", []) if isinstance(e, dict)]
                non_matching = [e for e in events_data.get("non_matching_events", []) if isinstance(e, dict)]
                all_events = matching + non_matching
                logger.info("  Matching: %d, Non-matching: %d", len(matching), len(non_matching))
                ps.save_json("matching_events.json", matching)
                ps.save_json("non_matching_events.json", non_matching)
            except Exception as exc:
                logger.error("  Event generation failed: %s", exc)
                continue

            # 5. Observe -> promote -> expire
            logger.info("  Running observe/promote/expire...")
            state = SpeculativeState(active=list(speculations))
            state, match_count = observe_events(all_events, state)
            logger.info("  Match count: %d", match_count)

            # Simulate TTL expiry: fast-forward past TTL for unconfirmed
            future_now = now + timedelta(days=15)
            promoted, state = promote_ready(state)
            rejected, state = expire_stale(state, future_now, cooldown_days=30)

            logger.info("  Promoted: %d, Rejected: %d, Active: %d",
                        len(promoted), len(rejected), len(state.active))
            ps.save_json("lifecycle_result.json", {
                "promoted": [s.to_dict() for s in promoted],
                "rejected": [s.to_dict() for s in rejected],
                "remaining": [s.to_dict() for s in state.active],
                "match_count": match_count,
            })

            # 6. Evaluate
            logger.info("  Evaluating...")
            confirmation_results = {
                s.domain: s.status == "promoted" for s in promoted
            }
            for s in rejected:
                confirmation_results[s.domain] = False
            for s in state.active:
                confirmation_results[s.domain] = False

            report = await evaluator.evaluate(
                speculations, profile, confirmation_results,
                persona_judgment=persona_judgment,
            )
            train_reports.append(report)
            ps.save_json("eval_report.json", report.to_dict())
            logger.info("  Score: %.3f", report.overall_score)
            logger.info(
                "    plaus=%.2f novel=%.2f spec=%.2f conf=%.2f "
                "no_hal=%.2f div=%.2f reson=%.2f",
                report.mean_plausibility, report.mean_novelty,
                report.mean_specificity, report.confirmation_rate,
                report.mean_no_hallucination, report.diversity_score,
                report.mean_persona_resonance,
            )

        if not train_reports:
            logger.warning("  No valid evaluations this epoch, skipping")
            continue

        # 7. Aggregate
        train_mean = sum(r.overall_score for r in train_reports) / len(train_reports)
        avg_conf = sum(r.confirmation_rate for r in train_reports) / len(train_reports)
        avg_div = sum(r.diversity_score for r in train_reports) / len(train_reports)
        avg_res = sum(r.mean_persona_resonance for r in train_reports) / len(train_reports)
        logger.info(
            "Epoch %d mean: %.3f, conf=%.3f, div=%.3f, resonance=%.3f",
            epoch, train_mean, avg_conf, avg_div, avg_res,
        )

        # Collect worst dimensions
        dim_totals: dict[str, float] = {}
        dim_counts: dict[str, int] = {}
        for report in train_reports:
            for d in report.worst_dimensions:
                key = d["dimension"]
                dim_totals[key] = dim_totals.get(key, 0) + d["score"]
                dim_counts[key] = dim_counts.get(key, 0) + 1
        worst_dims = sorted(
            [
                {"dimension": k, "score": dim_totals[k] / dim_counts[k]}
                for k in dim_totals
            ],
            key=lambda d: d["score"],
        )[:3]

        logger.info("Weakest dimensions:")
        for d in worst_dims:
            logger.info("  %s: %.2f", d["dimension"], d["score"])

        # 8. Epoch 1 is baseline-only: record score without optimizing
        if epoch == 1:
            best_score = train_mean
            epoch_result = {
                "epoch": epoch,
                "train_mean": round(train_mean, 4),
                "conf_rate": round(avg_conf, 4),
                "diversity": round(avg_div, 4),
                "persona_resonance": round(avg_res, 4),
                "action": "BASELINE",
                "changes_applied": 0,
                "summary": "Baseline evaluation (no optimization)",
                "accepted": True,
            }
            logger.info("BASELINE — score: %.3f (will optimize from epoch 2)", best_score)
            history_log.append(epoch_result)
            continue

        # 9. Optimize (epoch >= 2)
        is_explore = random.random() < args.explore_rate
        action = "EXPLORE" if is_explore else "EXPLOIT"
        logger.info("Strategy: %s", action)

        logger.info("Running optimizer agent...")
        combined_report = {
            "task": "speculation_generation",
            "train_mean": train_mean,
            "confirmation_rate": avg_conf,
            "diversity_score": avg_div,
            "persona_resonance": avg_res,
            "worst_fields": [
                {"layer": "speculation", "field": d["dimension"],
                 "score": d["score"], "deviation": f"{d['dimension']} too low"}
                for d in worst_dims
            ],
            "action": action,
            "note": (
                "Optimize build_speculation_generation_prompt. "
                "Focus on diversity (categories must differ), "
                "persona resonance (would the person actually click?), "
                "and bridge quality (near/far/novel mix)."
            ),
        }

        optimization = await run_optimizer_agent(combined_report, PROJECT_ROOT)
        raw_changes = optimization.get("changes", [])
        summary = optimization.get("summary", "No suggestions")

        epoch_d = rl.epoch_dir(epoch)
        opt_step = RunStep(epoch_d / "optimizer", "optimizer")
        opt_step.save_json("eval_input.json", combined_report)
        opt_step.save_json("optimizer_output.json", optimization)

        logger.info("Suggestion: %s", summary[:80])
        logger.info("Changes: %d", len(raw_changes))

        # 10. Apply changes
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
            logger.info("Proposed %d changes, applied %d", len(param_changes), applied_count)
            if applied_count < len(param_changes):
                logger.warning("%d changes did not match", len(param_changes) - applied_count)
            for pc in param_changes:
                logger.info("  %s: %s", pc.file_path, pc.description[:60])

            if applied_count > 0 and optimizer.has_pipeline_changes():
                logger.info("Pipeline code modified, running pytest...")
                passed, test_output = optimizer.validate_with_tests()
                if not passed:
                    optimizer.rollback()
                    logger.error("Tests failed, rolled back: %s", test_output[:100])
                    applied_count = 0
                else:
                    logger.info("Tests passed")

        # 11. Accept/rollback
        epoch_result = {
            "epoch": epoch,
            "train_mean": round(train_mean, 4),
            "conf_rate": round(avg_conf, 4),
            "diversity": round(avg_div, 4),
            "persona_resonance": round(avg_res, 4),
            "action": action,
            "changes_applied": applied_count,
            "summary": summary,
        }

        if train_mean > best_score:
            best_score = train_mean
            patience = 0
            epoch_result["accepted"] = True
            if applied_count > 0:
                optimizer.commit()
                logger.info("ACCEPT + COMMIT (%d changes) — new best: %.3f",
                            applied_count, best_score)
            else:
                logger.info("ACCEPT (no effective changes) — new best: %.3f", best_score)
        else:
            patience += 1
            epoch_result["accepted"] = False
            if applied_count > 0:
                optimizer.rollback()
                logger.info("ROLLBACK (%d changes) — (%.3f <= %.3f), patience=%d/3",
                            applied_count, train_mean, best_score, patience)
            else:
                logger.info("No improvement (%.3f <= %.3f), patience=%d/3",
                            train_mean, best_score, patience)

        history_log.append(epoch_result)

        if patience >= 3:
            logger.info("Early stopping")
            break

    # Summary
    logger.info("=" * 60)
    logger.info("Optimization complete")
    logger.info("=" * 60)

    result = {
        "task": "speculation_generation",
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

    logger.info(render_speculation_training_summary(result))

    rl.finish(best_score=best_score, epochs_run=len(history_log))
    logger.info("Full log: %s", rl.run_dir)

    report_dir = Path("data/eval/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = report_dir / f"speculation_optimize_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("Report: %s", path)


if __name__ == "__main__":
    asyncio.run(main())
