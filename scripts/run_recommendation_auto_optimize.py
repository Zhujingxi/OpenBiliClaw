"""Run automated self-optimization loop for recommendation module.

SGD/RL loop: generate persona → simulate discovery pool → run recommendation
→ evaluate diversity/expression/dedup quality → optimize prompts → validate.

Usage:
    .venv/bin/python scripts/run_recommendation_auto_optimize.py [--rounds 3] [--batch 2]
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

logger = logging.getLogger("eval.rec_optimize")


async def generate_mock_pool(
    persona: Any,
    llm_service: Any,
    count: int = 20,
) -> list[dict[str, Any]]:
    """Generate a simulated discovery pool for a persona."""
    from openbiliclaw.eval.agents import collect_json

    from claude_agent_sdk import ClaudeAgentOptions

    profile_ctx = persona.to_llm_context() if hasattr(persona, "to_llm_context") else str(persona)

    result = await collect_json(
        prompt=(
            f"根据以下用户画像，生成 {count} 条 B站 视频作为推荐候选池。\n\n"
            f"要求：\n"
            f"1. 每条包含 bvid, title, up_name, description, source_strategy, "
            f"relevance_score, topic_group, style_key\n"
            f"2. source_strategy 从 search/trending/related_chain/explore 中选\n"
            f"3. style_key 从 game_strategy/news_brief/practical_guide/story_doc/"
            f"visual_showcase/tech_analysis/deep_dive/fun_variety/lifestyle/review_roundup/light_chat 中选\n"
            f"4. topic_group 用 2-4 个中文词\n"
            f"5. 内容要多样：至少覆盖 4 个不同 topic_group 和 3 种 style_key\n"
            f"6. relevance_score 在 0.5-0.95 之间，不要全给高分\n\n"
            f'返回 JSON: {{"pool": [{{"bvid": "BV...", "title": "...", ...}}]}}\n\n'
            f"画像:\n{profile_ctx}"
        ),
        options=ClaudeAgentOptions(
            system_prompt="你是推荐池模拟器。直接返回 ```json 代码块。",
            max_turns=1,
        ),
        max_retries=2,
    )
    pool = result.get("pool", [])
    return [item for item in pool if isinstance(item, dict) and item.get("title")]


async def evaluate_recommendations(
    recommendations: list[dict[str, Any]],
    persona: Any,
    pool: list[dict[str, Any]],
    llm_service: Any,
) -> dict[str, Any]:
    """Evaluate recommendation quality across multiple dimensions."""
    from openbiliclaw.eval.agents import collect_json

    from claude_agent_sdk import ClaudeAgentOptions

    profile_ctx = persona.to_llm_context() if hasattr(persona, "to_llm_context") else str(persona)
    rec_text = json.dumps(recommendations, ensure_ascii=False, indent=2)[:3000]
    pool_text = json.dumps(
        [{"title": p.get("title"), "topic_group": p.get("topic_group"), "style_key": p.get("style_key")}
         for p in pool[:10]],
        ensure_ascii=False,
    )

    result = await collect_json(
        prompt=(
            "请评估以下推荐结果的质量。每个维度打 0-1 分。\n\n"
            "## 评估维度\n"
            "1. **relevance** (0-1): 推荐内容是否匹配用户画像的兴趣和需求\n"
            "2. **diversity** (0-1): 推荐结果的主题和风格是否多样（不是全部同一个话题）\n"
            "3. **expression_quality** (0-1): 推荐语是否自然、个性化、像朋友推荐\n"
            "4. **dedup_quality** (0-1): 是否没有重复或近似重复的主题（1=完全不重复）\n"
            "5. **serendipity** (0-1): 是否有超出用户已有兴趣的惊喜内容\n\n"
            f"## 用户画像\n{profile_ctx[:1000]}\n\n"
            f"## 候选池概览\n{pool_text}\n\n"
            f"## 推荐结果\n{rec_text}\n\n"
            '返回 JSON: {"relevance": 0.8, "diversity": 0.7, "expression_quality": 0.8, '
            '"dedup_quality": 0.9, "serendipity": 0.6, "overall": 0.76, '
            '"worst_dimension": "serendipity", "feedback": "一句话反馈"}'
        ),
        options=ClaudeAgentOptions(
            system_prompt="你是推荐质量评估专家。客观打分，不要全给高分。只返回 JSON。",
            max_turns=2,
        ),
        max_retries=2,
    )
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description="Recommendation auto-optimization loop")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--explore-rate", type=float, default=0.2)
    args = parser.parse_args()

    from openbiliclaw.config import load_config
    from openbiliclaw.eval.agents import (
        ONION_PROFILE_SCHEMA,
        PARAM_CHANGE_SCHEMA,
        PERSONA_SCHEMA_HINT,
        collect_json,
        run_optimizer_agent,
    )
    from openbiliclaw.eval.optimizer import MODIFIABLE_FILES, PromptOptimizer
    from openbiliclaw.eval.persona_pool import PersonaPool
    from openbiliclaw.eval.run_logger import RunLogger
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.profile import OnionProfile
    from _model_runtime import build_script_model_bundle

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    cfg = load_config()
    memory = MemoryManager(PROJECT_ROOT / "data")
    memory.initialize()
    llm_service = build_script_model_bundle(cfg, memory).llm_service

    optimizer = PromptOptimizer(project_root=PROJECT_ROOT)
    persona_pool = PersonaPool()
    rl = RunLogger(task="rec_auto", data_dir=Path("data"))

    personas_pool = [
        {"mbti": "INTJ", "depth": "hardcore"},
        {"mbti": "ENFP", "depth": "casual"},
        {"mbti": "ISTP", "depth": "moderate"},
        {"mbti": "INFJ", "depth": "hardcore"},
    ]

    best_score = 0.0
    patience = 0
    history_log: list[dict[str, Any]] = []

    logger.info("=" * 60)
    logger.info("推荐模块自动优化循环")
    logger.info("轮次: %d, batch: %d, 探索率: %.1f", args.rounds, args.batch, args.explore_rate)
    logger.info("=" * 60)

    for epoch in range(1, args.rounds + 1):
        logger.info("━" * 60)
        logger.info("Epoch %d/%d", epoch, args.rounds)

        train_reports = []
        batch_constraints = random.sample(personas_pool, min(args.batch, len(personas_pool)))

        for i, constraints in enumerate(batch_constraints, 1):
            logger.info("[%d.%d] Persona: %s", epoch, i, constraints)

            # 1. Generate persona
            from claude_agent_sdk import ClaudeAgentOptions

            gt_data = persona_pool.load_matching("rec", constraints)
            if gt_data is None:
                logger.info("  → 生成 persona...")
                try:
                    gt_data = await collect_json(
                        prompt=(
                            f"生成一个虚构的 B站 用户画像，约束条件：{json.dumps(constraints, ensure_ascii=False)}\n"
                            f"请返回 JSON 代码块，personality_portrait 至少 200 字，likes 至少 3 个 domain。\n\n"
                            f"{PERSONA_SCHEMA_HINT}"
                        ),
                        options=ClaudeAgentOptions(
                            system_prompt="你是用户画像生成器。直接返回 ```json 代码块。",
                            max_turns=1,
                        ),
                        max_retries=2,
                        json_schema=ONION_PROFILE_SCHEMA,
                    )
                    persona_pool.save("rec", constraints, gt_data)
                except Exception as exc:
                    logger.error("  ❌ Persona 生成失败: %s", exc)
                    continue

            try:
                persona = OnionProfile.from_dict(gt_data)
            except Exception as exc:
                logger.error("  ❌ Persona 解析失败: %s", exc)
                continue

            # 2. Generate mock discovery pool
            logger.info("  → 生成模拟候选池...")
            try:
                pool = await generate_mock_pool(persona, llm_service, count=20)
                logger.info("  ✅ 候选池: %d 条", len(pool))
            except Exception as exc:
                logger.error("  ❌ 候选池生成失败: %s", exc)
                continue

            if len(pool) < 5:
                logger.warning("  ⚠️ 候选池太少，跳过")
                continue

            # 3. Simulate recommendation selection + expression
            # Use the recommendation engine's diversification logic
            import tempfile

            from openbiliclaw.discovery.engine import DiscoveredContent
            from openbiliclaw.recommendation.engine import RecommendationEngine

            # Build DiscoveredContent objects from pool
            discovered_items: list[DiscoveredContent] = []
            for idx, item in enumerate(pool):
                dc = DiscoveredContent(
                    bvid=str(item.get("bvid", f"BV_mock_{idx}")),
                    title=str(item.get("title", "")),
                    up_name=str(item.get("up_name", "")),
                    description=str(item.get("description", "")),
                    relevance_score=float(item.get("relevance_score", 0.7)),
                    topic_group=str(item.get("topic_group", "")),
                    topic_key=str(item.get("topic_group", "")),
                    style_key=str(item.get("style_key", "light_chat")),
                    source_strategy=str(item.get("source_strategy", "search")),
                )
                discovered_items.append(dc)

            # Run diversified batch selection
            selected = RecommendationEngine._select_diversified_batch(
                discovered_items, limit=5,
            )

            # Generate expressions for selected items
            rec_results: list[dict[str, Any]] = []
            with tempfile.TemporaryDirectory() as tmpdir:
                from openbiliclaw.storage.database import Database
                db = Database(Path(tmpdir) / "rec.db")
                db.initialize()

                # Build embedding service if available
                emb_service = None
                try:
                    from openbiliclaw.llm.embedding import EmbeddingService
                    from openbiliclaw.llm.gemini_provider import GeminiProvider
                    g = registry.get("gemini")
                    if isinstance(g, GeminiProvider):
                        emb_service = EmbeddingService(g)
                except Exception:
                    pass

                rec_engine = RecommendationEngine(
                    llm=llm_service, database=db, embedding_service=emb_service,
                )

                soul_profile = persona.to_soul_profile() if hasattr(persona, "to_soul_profile") else None
                if soul_profile is None:
                    from openbiliclaw.soul.profile import SoulProfile
                    soul_profile = SoulProfile(
                        personality_portrait=persona.personality_portrait,
                        core_traits=persona.core.core_traits,
                        deep_needs=persona.core.deep_needs,
                    )

                for item in selected:
                    try:
                        expr, topic_label = await rec_engine.generate_expression(item, soul_profile)
                    except Exception:
                        expr = ""
                        topic_label = ""
                    rec_results.append({
                        "title": item.title,
                        "up_name": item.up_name,
                        "topic_group": item.topic_group,
                        "style_key": item.style_key,
                        "source_strategy": item.source_strategy,
                        "relevance_score": item.relevance_score,
                        "expression": expr,
                        "topic_label": topic_label,
                    })

            logger.info("  ✅ 推荐 %d 条，含表达", len(rec_results))

            # 4. Evaluate
            logger.info("  → 评估推荐质量...")
            try:
                report = await evaluate_recommendations(
                    rec_results, persona, pool, llm_service,
                )
                overall = float(report.get("overall", 0.0))
                train_reports.append(report)
                logger.info("  📊 Score: %.3f", overall)
                for dim in ["relevance", "diversity", "expression_quality", "dedup_quality", "serendipity"]:
                    val = report.get(dim, 0.0)
                    icon = "✅" if val >= 0.8 else "⚠️" if val >= 0.5 else "❌"
                    logger.info("    %s %s: %.2f", icon, dim, val)
            except Exception as exc:
                logger.error("  ❌ 评估失败: %s", exc)
                continue

        if not train_reports:
            logger.warning("  ❌ 本轮无有效评估，跳过")
            continue

        # 5. Compute train mean
        train_mean = sum(float(r.get("overall", 0)) for r in train_reports) / len(train_reports)
        logger.info("📈 Epoch %d train mean: %.3f", epoch, train_mean)

        # 6. Collect worst dimensions
        worst_fields = []
        for r in train_reports:
            for dim in ["relevance", "diversity", "expression_quality", "dedup_quality", "serendipity"]:
                val = float(r.get(dim, 0))
                if val < 0.8:
                    worst_fields.append({
                        "layer": "recommendation",
                        "field": dim,
                        "score": val,
                        "deviation": str(r.get("feedback", "")),
                    })
        worst_fields.sort(key=lambda f: f["score"])
        worst_fields = worst_fields[:5]

        # 7. Epoch 1 is baseline-only: record score without optimizing
        if epoch == 1:
            best_score = train_mean
            history_log.append({
                "epoch": epoch,
                "train_mean": round(train_mean, 4),
                "action": "BASELINE",
                "changes_applied": 0,
            })
            logger.info("📊 BASELINE — score: %.3f (will optimize from epoch 2)", best_score)
            continue

        # 8. Run optimizer (epoch >= 2)
        action = "EXPLORE" if random.random() < args.explore_rate else "EXPLOIT"
        logger.info("策略: %s", action)
        logger.info("→ 运行 Optimizer Agent...")

        combined_report = {
            "task": "recommendation",
            "train_mean": train_mean,
            "worst_fields": worst_fields,
            "action": action,
            "modifiable_files": [
                "src/openbiliclaw/llm/prompts.py",
                "src/openbiliclaw/recommendation/engine.py",
                "src/openbiliclaw/recommendation/curator.py",
            ],
        }

        optimization = await run_optimizer_agent(combined_report, PROJECT_ROOT)
        raw_changes = optimization.get("changes", [])
        summary = optimization.get("summary", "无建议")
        logger.info("建议: %s", summary[:80])
        logger.info("修改数: %d", len(raw_changes))

        # 9. Apply
        from openbiliclaw.eval.optimizer import ParamChange
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

            if applied_count > 0 and optimizer.has_pipeline_changes():
                passed, test_output = optimizer.validate_with_tests()
                if not passed:
                    optimizer.rollback()
                    logger.error("❌ 测试失败，已回滚")
                    applied_count = 0
                else:
                    logger.info("✅ 测试通过")

        # 10. Accept/rollback
        if train_mean > best_score:
            best_score = train_mean
            patience = 0
            if applied_count > 0:
                optimizer.commit()
                logger.info("✅ ACCEPT + COMMIT — 新最佳: %.3f", best_score)
            else:
                logger.info("✅ ACCEPT (无有效修改) — 新最佳: %.3f", best_score)
        else:
            patience += 1
            if applied_count > 0:
                optimizer.rollback()
                logger.info("↩️ ROLLBACK — (%.3f <= %.3f)", train_mean, best_score)
            else:
                logger.info("↩️ 未超越最佳 (%.3f <= %.3f)", train_mean, best_score)

        history_log.append({
            "epoch": epoch,
            "train_mean": round(train_mean, 4),
            "action": action,
            "changes_applied": applied_count,
        })

        if patience >= 3:
            logger.info("⛔ Early stopping")
            break

    # Summary
    logger.info("=" * 60)
    logger.info("Best score: %.3f", best_score)
    for h in history_log:
        logger.info("  Epoch %d: %.4f (%s, %d changes)",
                     h["epoch"], h["train_mean"], h["action"], h["changes_applied"])

    rl.finish(best_score=best_score, epochs_run=len(history_log))
    logger.info("完整日志: %s", rl.run_dir)


if __name__ == "__main__":
    asyncio.run(main())
