"""Run human-in-the-loop evaluation for discovery strategies.

Displays discovery results interactively for human scoring,
then generates a DiscoveryEvalReport.

Usage:
    .venv/bin/python scripts/run_discovery_eval.py [--mock] [--strategy search]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("eval.discovery_human")


def _prompt_score(label: str, default: float = 0.5) -> float:
    """Prompt user for a 0-1 score."""
    while True:
        raw = input(f"  {label} (0.0-1.0) [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = float(raw)
            if 0.0 <= value <= 1.0:
                return value
            print("    请输入 0.0 到 1.0 之间的数值")
        except ValueError:
            print("    无效输入，请输入数字")


def _prompt_yn(label: str, default: bool = True) -> bool:
    """Prompt user for yes/no."""
    suffix = "[Y/n]" if default else "[y/N]"
    raw = input(f"  {label} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "是")


async def run_with_mock(
    llm_service: Any,
    strategies_to_eval: list[str],
) -> tuple[dict[str, list[Any]], dict[str, dict[str, object]], Any]:
    """Run discovery against a mock scenario."""
    from openbiliclaw.eval.discovery_scenario import ScenarioGenerator, ScenarioPool
    from openbiliclaw.eval.persona_pool import PersonaPool
    from openbiliclaw.soul.profile import OnionProfile

    persona_pool = PersonaPool(PROJECT_ROOT / "data" / "eval" / "persona_pool" / "discovery")
    scenario_pool = ScenarioPool(PROJECT_ROOT / "data" / "eval" / "scenario_pool")

    persona = persona_pool.load_any()
    if persona is None:
        print("No cached personas found. Generating one...")
        from openbiliclaw.eval.agents import collect_json
        persona_data = await collect_json(
            prompt="生成一个完整的 B 站用户画像（OnionProfile 格式），包含所有层。",
            json_schema='{"personality_portrait": "...", "core": {...}, ...}',
        )
        persona = OnionProfile.from_dict(persona_data)
        persona_pool.save(persona, constraints={})

    from openbiliclaw.eval.discovery_scenario import _persona_signature
    pid = _persona_signature(persona)
    scenario = scenario_pool.load(pid)
    if scenario is None:
        print(f"Generating scenario for persona {pid[:8]}...")
        gen = ScenarioGenerator(llm_service)
        scenario = await gen.generate(persona)
        if scenario.content_pool:
            scenario_pool.save(scenario)

    # Import here to avoid circular at top level
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from run_discovery_auto_optimize import run_discovery_pipeline

    strategy_results, intermediates = await run_discovery_pipeline(
        persona, scenario, llm_service,
    )
    filtered = {k: v for k, v in strategy_results.items() if k in strategies_to_eval}
    filtered_inter = {k: v for k, v in intermediates.items() if k in strategies_to_eval}
    return filtered, filtered_inter, persona


def display_and_collect_feedback(
    strategy_results: dict[str, list[Any]],
    intermediates: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    """Display results and collect human feedback interactively."""
    from openbiliclaw.eval.discovery_evaluator import _STRATEGY_DIMENSIONS

    feedback: dict[str, dict[str, object]] = {}

    for strategy_name, results in strategy_results.items():
        print(f"\n{'='*60}")
        print(f"  {strategy_name.upper()} ({len(results)} items)")
        print(f"{'='*60}")

        inter = intermediates.get(strategy_name, {})
        if "queries" in inter:
            print(f"  生成的搜索词: {inter['queries']}")
        if "rids" in inter:
            print(f"  选择的分区: {inter['rids']}")
        if "domains" in inter:
            domains = inter["domains"]
            if isinstance(domains, list):
                for d in domains[:3]:
                    if isinstance(d, dict):
                        print(f"  域: {d.get('domain', '?')} (novelty={d.get('novelty_level', '?')})")
        if "seeds" in inter:
            seeds = inter["seeds"]
            if isinstance(seeds, list):
                print(f"  种子: {[s[0] if isinstance(s, (list, tuple)) else s for s in seeds[:5]]}")

        print()
        for idx, item in enumerate(results[:10], 1):
            title = getattr(item, "title", "?")
            up_name = getattr(item, "up_name", "?")
            score = getattr(item, "relevance_score", 0)
            reason = getattr(item, "relevance_reason", "")
            views = getattr(item, "view_count", 0)
            source = getattr(item, "source_strategy", "")
            print(f"  [{idx}] {title}")
            print(f"      UP: {up_name} | 播放: {views} | 来源: {source}")
            print(f"      系统评分: {score:.2f} | 理由: {reason[:80]}")
            print()

        print(f"--- 请为 {strategy_name} 打分 ---")
        dims = _STRATEGY_DIMENSIONS.get(strategy_name, [])
        for dim in dims:
            key = f"{strategy_name}.{dim}"
            score = _prompt_score(f"{dim}", default=0.5)
            note = input(f"  {dim} 备注 (可跳过): ").strip()
            feedback[key] = {"score": score, "note": note}

    # Cross-strategy diversity
    print(f"\n{'='*60}")
    print("  跨策略多样性评估")
    print(f"{'='*60}")
    cross_score = _prompt_score("cross.diversity", default=0.5)
    feedback["cross.diversity"] = {"score": cross_score, "note": ""}

    return feedback


async def main(args: argparse.Namespace) -> None:
    """Main human eval flow."""
    from openbiliclaw.config import load_config
    from openbiliclaw.eval.discovery_evaluator import DiscoveryEvalReport, DiscoveryEvaluator
    from openbiliclaw.eval.run_logger import RunLogger
    from openbiliclaw.memory.manager import MemoryManager
    from _model_runtime import build_script_model_bundle

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    cfg = load_config()
    memory = MemoryManager(PROJECT_ROOT / "data")
    memory.initialize()
    llm_service = build_script_model_bundle(cfg, memory).llm_service

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]

    print("\n🔍 Discovery Human Evaluation")
    print(f"   Strategies: {strategies}")
    print(f"   Mode: {'mock' if args.mock else 'live'}\n")

    if args.mock:
        strategy_results, intermediates, persona = await run_with_mock(
            llm_service, strategies,
        )
    else:
        print("Live mode not yet implemented. Use --mock flag.")
        return

    # Display and collect feedback
    feedback = display_and_collect_feedback(strategy_results, intermediates)

    # Build report
    evaluator = DiscoveryEvaluator(llm_service=llm_service)
    report = await evaluator.evaluate_with_human(strategy_results, feedback)

    # Display report
    print(f"\n{'='*60}")
    print("  评估报告")
    print(f"{'='*60}")
    print(f"  总分: {report.overall_score:.4f}")
    for name, strat_report in report.strategy_reports.items():
        print(f"\n  {name} ({strat_report.overall_score:.2f}):")
        for d in strat_report.dimension_scores:
            mark = "✅" if d.score >= 0.8 else "⚠️" if d.score >= 0.5 else "❌"
            print(f"    {mark} {d.dimension}: {d.score:.2f} {d.details}")

    print(f"\n  最差维度:")
    for d in report.worst_dimensions:
        param = "?"
        from openbiliclaw.eval.discovery_evaluator import DISCOVERY_FIELD_TO_PARAM
        param = DISCOVERY_FIELD_TO_PARAM.get(d.dimension, "unknown")
        print(f"    {d.dimension} = {d.score:.2f} → {param}")

    # Save
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_logger = RunLogger(PROJECT_ROOT / "data" / "eval" / "runs" / f"discovery_human_{ts}")
    step = run_logger.step("eval")
    step.save_json("feedback.json", feedback)
    step.save_json("report.json", {
        "overall_score": report.overall_score,
        "strategy_scores": {k: v.overall_score for k, v in report.strategy_reports.items()},
        "worst_dimensions": [{"dim": d.dimension, "score": d.score} for d in report.worst_dimensions],
    })
    print(f"\n  报告已保存到 {run_logger.base_dir}")

    # Optionally trigger optimization
    if _prompt_yn("\n是否触发一轮优化?", default=False):
        from openbiliclaw.eval.discovery_optimizer import (
            create_discovery_optimizer,
            dimension_scores_to_field_scores,
        )
        optimizer = create_discovery_optimizer(
            project_root=PROJECT_ROOT,
            use_agent_sdk=True,
        )
        field_scores = dimension_scores_to_field_scores(report.worst_dimensions)
        changes = await optimizer.exploit(field_scores)
        if changes:
            applied = optimizer.apply(changes)
            print(f"  Applied {applied} changes")
            if _prompt_yn("  确认接受这些修改?", default=False):
                optimizer.commit()
                print("  ✅ Changes committed")
            else:
                optimizer.rollback()
                print("  ❌ Changes rolled back")
        else:
            print("  No changes proposed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discovery human evaluation")
    parser.add_argument("--mock", action="store_true", default=False,
                        help="Use mock Bilibili client")
    parser.add_argument("--strategies", type=str, default="search,trending,explore,related_chain",
                        help="Comma-separated strategy names")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
