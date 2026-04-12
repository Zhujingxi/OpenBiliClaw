"""Run automated self-optimization loop for content discovery strategies.

SGD/RL loop: generate persona → generate scenario → run discovery →
evaluate quality dimensions → optimize prompts → validate → accept/rollback.

Usage:
    .venv/bin/python scripts/run_discovery_auto_optimize.py [--rounds 10] [--batch 3]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("eval.discovery_optimize")


async def run_discovery_pipeline(
    persona: Any,
    scenario: Any,
    llm_service: Any,
) -> tuple[dict[str, list[Any]], dict[str, dict[str, object]]]:
    """Run all 4 discovery strategies against a mock scenario.

    Returns (strategy_results, intermediates).
    """
    from openbiliclaw.discovery.engine import (
        ContentDiscoveryEngine,
        DiscoveryConcurrencyController,
    )
    from openbiliclaw.discovery.strategies.strategies import (
        ExploreStrategy,
        RelatedChainStrategy,
        SearchStrategy,
        TrendingStrategy,
    )
    from openbiliclaw.eval.discovery_scenario import MockBilibiliClient, MockMemoryManager

    mock_client = MockBilibiliClient(scenario)
    mock_memory = MockMemoryManager(scenario)
    concurrency = DiscoveryConcurrencyController(
        bilibili_request_concurrency=4,
        llm_evaluation_concurrency=4,
    )

    search = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=mock_client,
        concurrency=concurrency,
    )
    trending = TrendingStrategy(
        bilibili_client=mock_client,
        llm_service=llm_service,
        concurrency=concurrency,
    )
    related = RelatedChainStrategy(
        bilibili_client=mock_client,
        llm_service=llm_service,
        memory_manager=mock_memory,
        search_strategy=search,
        concurrency=concurrency,
    )
    explore = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=mock_client,
        concurrency=concurrency,
    )

    # Get SoulProfile from persona (OnionProfile)
    if hasattr(persona, "to_soul_profile"):
        profile = persona.to_soul_profile()
    elif hasattr(persona, "preferences"):
        profile = persona
    else:
        from openbiliclaw.soul.profile import SoulProfile
        profile = SoulProfile()

    strategy_results: dict[str, list[Any]] = {}
    strategies = {"search": search, "trending": trending, "related_chain": related, "explore": explore}

    for name, strategy in strategies.items():
        try:
            results = await strategy.discover(profile, limit=15)
            strategy_results[name] = results
            logger.info("  Strategy '%s': %d items", name, len(results))
        except Exception:
            logger.exception("  Strategy '%s' failed", name)
            strategy_results[name] = []

    intermediates: dict[str, dict[str, object]] = {}
    for name, strategy in strategies.items():
        intermediates[name] = getattr(strategy, "last_intermediates", {})

    return strategy_results, intermediates


async def main(args: argparse.Namespace) -> None:
    """Main optimization loop."""
    from openbiliclaw.config import load_config
    from openbiliclaw.eval.discovery_evaluator import DiscoveryEvaluator
    from openbiliclaw.eval.discovery_optimizer import (
        create_discovery_optimizer,
        dimension_scores_to_field_scores,
    )
    from openbiliclaw.eval.discovery_scenario import ScenarioGenerator, ScenarioPool
    from openbiliclaw.eval.persona_pool import PersonaPool
    from openbiliclaw.eval.run_logger import RunLogger
    from openbiliclaw.llm.registry import build_llm_registry

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    cfg = load_config()
    registry = build_llm_registry(cfg)

    # Build LLMService (needs a MemoryManager for core memory injection)
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.memory.manager import MemoryManager

    memory = MemoryManager(PROJECT_ROOT / "data")
    memory.initialize()
    llm_service = LLMService(registry=registry, memory=memory)

    evaluator = DiscoveryEvaluator(llm_service=llm_service)
    optimizer = create_discovery_optimizer(project_root=PROJECT_ROOT, use_agent_sdk=args.use_agent)
    scenario_gen = ScenarioGenerator(llm_service)
    scenario_pool = ScenarioPool(PROJECT_ROOT / "data" / "eval" / "scenario_pool")
    persona_pool = PersonaPool(PROJECT_ROOT / "data" / "eval" / "persona_pool" / "discovery")

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_logger = RunLogger(task="discovery_auto", data_dir=PROJECT_ROOT / "data", run_id=ts)

    best_score = 0.0
    best_epoch = 0
    patience = 0
    epoch_history: list[dict[str, Any]] = []

    strategies_to_eval = [s.strip() for s in args.strategies.split(",") if s.strip()]

    logger.info("=== Discovery Auto-Optimize ===")
    logger.info("Rounds: %d | Batch: %d | Explore rate: %.2f | Strategies: %s",
                args.rounds, args.batch, args.explore_rate, strategies_to_eval)

    for epoch in range(1, args.rounds + 1):
        logger.info("\n--- Epoch %d/%d ---", epoch, args.rounds)

        reports = []
        for persona_idx in range(args.batch):
            logger.info("[Epoch %d] Persona %d/%d", epoch, persona_idx + 1, args.batch)

            # 1. Generate or load persona
            from openbiliclaw.soul.profile import OnionProfile, SoulProfile

            persona: OnionProfile | None = None
            persona_data = persona_pool.load_any("discovery")
            if persona_data is not None:
                try:
                    persona = OnionProfile.from_dict(persona_data)
                    logger.info("  Loaded persona from pool")
                except Exception:
                    logger.warning("  Failed to parse cached persona, generating new one")

            if persona is None:
                logger.info("  Generating new persona via LLM...")
                constraints = {"mbti": random.choice(["INTJ", "ENFP", "ISTP", "INFJ"]),
                               "depth": random.choice(["hardcore", "casual", "moderate"])}
                try:
                    response = await llm_service.complete_structured_task(
                        system_instruction=(
                            "生成一个完整的 B 站用户画像。输出严格 JSON，包含:\n"
                            "personality_portrait(>=200字), core_traits(3-6条), "
                            "deep_needs(3-5条), values(3-5条), "
                            "interests(5-10个,含name/category/weight), "
                            "favorite_up_users(1-3个), exploration_openness(0-1)。"
                        ),
                        user_input=f"约束: {json.dumps(constraints, ensure_ascii=False)}",
                        temperature=0.9,
                        max_tokens=4096,
                    )
                    raw = json.loads(str(getattr(response, "content", "")).strip())
                    persona = OnionProfile.from_dict(raw)
                    persona_pool.save("discovery", constraints, raw)
                except Exception:
                    logger.exception("  Persona generation failed, skipping")
                    continue

            # 2. Generate or load scenario
            from openbiliclaw.eval.discovery_scenario import _persona_signature
            pid = _persona_signature(persona)
            scenario = scenario_pool.load(pid)
            if scenario is None:
                logger.info("  Generating scenario for persona %s...", pid[:8])
                scenario = await scenario_gen.generate(persona)
                if scenario.content_pool:
                    scenario_pool.save(scenario)
                else:
                    logger.warning("  Scenario generation returned empty pool, skipping")
                    continue

            logger.info("  Scenario: %d videos, %d events",
                        len(scenario.content_pool), len(scenario.mock_event_history))

            # 3. Run discovery
            strategy_results, intermediates = await run_discovery_pipeline(
                persona, scenario, llm_service,
            )

            # Filter to requested strategies
            filtered_results = {
                k: v for k, v in strategy_results.items()
                if k in strategies_to_eval
            }
            filtered_intermediates = {
                k: v for k, v in intermediates.items()
                if k in strategies_to_eval
            }
            # Inject scenario ground truth labels for filter_precision scoring
            for k in filtered_intermediates:
                filtered_intermediates[k]["relevance_labels"] = scenario.relevance_labels

            # 4. Evaluate
            report = await evaluator.evaluate_all(
                filtered_results,
                persona,
                intermediates=filtered_intermediates,
            )
            reports.append(report)

            # Log results
            for strat_name, strat_report in report.strategy_reports.items():
                dims_str = " | ".join(
                    f"{d.dimension}={'✅' if d.score >= 0.8 else '⚠️' if d.score >= 0.5 else '❌'}{d.score:.2f}"
                    for d in strat_report.dimension_scores
                )
                logger.info("  %s (%.2f): %s", strat_name, strat_report.overall_score, dims_str)
            logger.info("  Overall: %.4f", report.overall_score)

            # Save artifacts
            step = run_logger.step(f"epoch{epoch}_persona{persona_idx}")
            step.save_json("strategy_results.json", {
                k: [{"bvid": item.bvid, "title": item.title, "score": item.relevance_score}
                    for item in v]
                for k, v in filtered_results.items()
            })
            step.save_json("intermediates.json", {
                k: _safe_serialize(v)
                for k, v in filtered_intermediates.items()
            })
            step.save_json("eval_report.json", {
                "overall_score": report.overall_score,
                "strategy_scores": {
                    k: v.overall_score for k, v in report.strategy_reports.items()
                },
                "worst_dimensions": [
                    {"dim": d.dimension, "score": d.score}
                    for d in report.worst_dimensions
                ],
            })

        if not reports:
            logger.warning("No reports generated for epoch %d, skipping", epoch)
            continue

        # 5. Aggregate
        train_mean = sum(r.overall_score for r in reports) / len(reports)
        all_worst = []
        for r in reports:
            all_worst.extend(r.worst_dimensions)
        worst_dims = sorted(all_worst, key=lambda d: d.score)[:5]

        logger.info("\n[Epoch %d] Train mean: %.4f | Worst dims:", epoch, train_mean)
        for d in worst_dims[:3]:
            logger.info("  %s = %.2f", d.dimension, d.score)

        # 6. Epoch 1 is baseline-only: record score without optimizing
        if epoch == 1:
            best_score = train_mean
            best_epoch = epoch
            epoch_history.append({
                "epoch": epoch,
                "train_mean": round(train_mean, 4),
                "action": "BASELINE",
                "changes_applied": 0,
                "accepted": True,
                "worst_3": [{"dim": d.dimension, "score": d.score} for d in worst_dims[:3]],
            })
            logger.info("[Epoch %d] 📊 BASELINE — score: %.4f (will optimize from epoch 2)", epoch, best_score)
            continue

        # 7. Exploit or Explore (epoch >= 2)
        action = "EXPLORE" if random.random() < args.explore_rate else "EXPLOIT"
        logger.info("[Epoch %d] Action: %s", epoch, action)

        field_scores = dimension_scores_to_field_scores(worst_dims)

        try:
            if action == "EXPLOIT":
                changes = await optimizer.exploit(field_scores)
            else:
                changes = await optimizer.explore()
        except Exception:
            logger.exception("[Epoch %d] Optimizer failed", epoch)
            changes = []

        # 8. Apply
        applied = 0
        if changes:
            applied = optimizer.apply(changes)
            logger.info("[Epoch %d] Applied %d/%d changes", epoch, applied, len(changes))

            if optimizer.has_pipeline_changes():
                passed, output = optimizer.validate_with_tests()
                if not passed:
                    logger.warning("[Epoch %d] Tests failed after changes, rolling back", epoch)
                    optimizer.rollback()
                    applied = 0

        # 9. Accept or rollback
        accepted = False
        if applied > 0 and train_mean > best_score:
            optimizer.commit()
            best_score = train_mean
            best_epoch = epoch
            patience = 0
            accepted = True
            logger.info("[Epoch %d] ✅ ACCEPTED (%.4f > previous best)", epoch, train_mean)
        elif applied > 0:
            optimizer.rollback()
            patience += 1
            logger.info("[Epoch %d] ❌ ROLLED BACK (%.4f <= best %.4f, patience=%d)",
                        epoch, train_mean, best_score, patience)
        else:
            patience += 1
            logger.info("[Epoch %d] No changes applied, patience=%d", epoch, patience)

        epoch_history.append({
            "epoch": epoch,
            "train_mean": round(train_mean, 4),
            "action": action,
            "changes_applied": applied,
            "accepted": accepted,
            "worst_3": [{"dim": d.dimension, "score": d.score} for d in worst_dims[:3]],
        })

        if patience >= args.patience:
            logger.info("Early stopping at epoch %d (patience=%d)", epoch, args.patience)
            break

    # Final summary
    logger.info("\n=== Training Summary ===")
    logger.info("Best score: %.4f (epoch %d)", best_score, best_epoch)
    for entry in epoch_history:
        mark = "✅" if entry["accepted"] else "❌"
        logger.info(
            "  Epoch %d: %.4f %s %s",
            entry["epoch"], entry["train_mean"], mark, entry["action"],
        )

    # Save final report
    report_path = run_logger.run_dir / "summary.json"
    report_path.write_text(json.dumps({
        "best_score": best_score,
        "best_epoch": best_epoch,
        "epochs": epoch_history,
        "strategies": strategies_to_eval,
        "config": {
            "rounds": args.rounds,
            "batch": args.batch,
            "explore_rate": args.explore_rate,
            "patience": args.patience,
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Report saved to %s", report_path)


def _safe_serialize(obj: Any) -> Any:
    """Convert objects to JSON-serializable form."""
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_serialize(v) for v in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discovery auto-optimization loop")
    parser.add_argument("--rounds", type=int, default=10, help="Max epochs")
    parser.add_argument("--batch", type=int, default=3, help="Personas per epoch")
    parser.add_argument("--explore-rate", type=float, default=0.2, help="Exploration probability")
    parser.add_argument("--patience", type=int, default=3, help="Early stop patience")
    parser.add_argument("--strategies", type=str, default="search,trending,explore,related_chain",
                        help="Comma-separated strategy names to evaluate")
    parser.add_argument("--use-agent", action="store_true", default=True,
                        help="Use Claude Agent SDK for optimization")
    parser.add_argument("--no-agent", action="store_false", dest="use_agent",
                        help="Use direct LLM for optimization")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
