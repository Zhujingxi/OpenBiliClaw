"""Batch-generate diverse personas and scenarios for discovery evaluation.

Usage:
    .venv/bin/python scripts/generate_discovery_personas.py [--count 6]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("eval.persona_gen")

# Diverse persona archetypes — cover different interest domains, depth levels, MBTI types
PERSONA_ARCHETYPES: list[dict[str, str]] = [
    {"mbti": "INTJ", "depth": "hardcore", "interest_breadth": "specialist",
     "hint": "重度知识类用户，偏好深度解析、纪录片、历史、科技，exploration_openness 低(0.3)"},
    {"mbti": "ENFP", "depth": "casual", "interest_breadth": "generalist",
     "hint": "轻度娱乐用户，喜欢综艺、搞笑、生活vlog、美食，exploration_openness 高(0.9)"},
    {"mbti": "ISTP", "depth": "moderate", "interest_breadth": "specialist",
     "hint": "技术宅，偏好编程、DIY、电子、游戏攻略，exploration_openness 中(0.5)"},
    {"mbti": "INFJ", "depth": "hardcore", "interest_breadth": "generalist",
     "hint": "人文爱好者，偏好哲学、心理学、文学、独立电影、纪录片，exploration_openness 高(0.7)"},
    {"mbti": "ESTJ", "depth": "casual", "interest_breadth": "specialist",
     "hint": "时事关注者，偏好国际新闻、财经、军事、时政评论，exploration_openness 低(0.2)"},
    {"mbti": "ENTP", "depth": "moderate", "interest_breadth": "generalist",
     "hint": "跨界探索者，偏好科普、AI、哲学、商业分析、辩论，exploration_openness 极高(0.95)"},
]


async def generate_one(
    llm_service: Any,
    archetype: dict[str, str],
    persona_pool: Any,
) -> dict[str, Any] | None:
    """Generate one persona from an archetype."""
    hint = archetype.get("hint", "")
    constraints = {k: v for k, v in archetype.items() if k != "hint"}

    try:
        response = await llm_service.complete_structured_task(
            system_instruction=(
                "生成一个完整的 B 站用户画像。输出严格 JSON，包含:\n"
                "personality_portrait(>=200字中文), core_traits(3-6条), "
                "deep_needs(3-5条), values(3-5条), "
                "interests(5-10个,含name/category/weight 0.0-1.0), "
                "favorite_up_users(1-3个真实B站UP主名), "
                "exploration_openness(0.0-1.0), "
                "cognitive_style(2-4条)。\n\n"
                "personality_portrait 必须是自然语言段落，不是列表。"
            ),
            user_input=(
                f"画像约束:\n{json.dumps(constraints, ensure_ascii=False)}\n\n"
                f"用户特征: {hint}"
            ),
            temperature=0.9,
            max_tokens=4096,
        )
        raw = json.loads(str(getattr(response, "content", "")).strip())
        persona_pool.save("discovery", constraints, raw)
        logger.info("  Generated: %s (%s)", constraints.get("mbti"), hint[:30])
        return raw
    except Exception:
        logger.exception("  Failed to generate persona for %s", constraints)
        return None


async def generate_scenario(
    llm_service: Any,
    persona_data: dict[str, Any],
    scenario_pool: Any,
) -> bool:
    """Generate a scenario for a persona."""
    from openbiliclaw.eval.discovery_scenario import ScenarioGenerator, _persona_signature
    from openbiliclaw.soul.profile import OnionProfile

    try:
        persona = OnionProfile.from_dict(persona_data)
    except Exception:
        logger.warning("  Failed to parse persona, skipping scenario")
        return False

    pid = _persona_signature(persona)
    if scenario_pool.load(pid) is not None:
        logger.info("  Scenario already cached for %s", pid[:8])
        return True

    gen = ScenarioGenerator(llm_service)
    scenario = await gen.generate(persona)
    if not scenario.content_pool:
        logger.warning("  Scenario generation returned empty pool for %s", pid[:8])
        return False

    scenario_pool.save(scenario)
    logger.info("  Scenario generated: %d videos for %s", len(scenario.content_pool), pid[:8])
    return True


async def main(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    from openbiliclaw.config import load_config
    from openbiliclaw.eval.discovery_scenario import ScenarioPool
    from openbiliclaw.eval.persona_pool import PersonaPool
    from openbiliclaw.memory.manager import MemoryManager
    from _model_runtime import build_script_model_bundle

    cfg = load_config()
    memory = MemoryManager(PROJECT_ROOT / "data")
    memory.initialize()
    llm_service = build_script_model_bundle(cfg, memory).llm_service

    persona_pool = PersonaPool(PROJECT_ROOT / "data" / "eval" / "persona_pool")
    scenario_pool = ScenarioPool(PROJECT_ROOT / "data" / "eval" / "scenario_pool")

    archetypes = PERSONA_ARCHETYPES[:args.count]

    logger.info("=== Generating %d diverse personas + scenarios ===", len(archetypes))

    personas: list[dict[str, Any]] = []
    for i, archetype in enumerate(archetypes, 1):
        logger.info("[%d/%d] Generating persona...", i, len(archetypes))
        data = await generate_one(llm_service, archetype, persona_pool)
        if data:
            personas.append(data)

    logger.info("\n=== Generating scenarios for %d personas ===", len(personas))
    success = 0
    for i, persona_data in enumerate(personas, 1):
        logger.info("[%d/%d] Generating scenario...", i, len(personas))
        if await generate_scenario(llm_service, persona_data, scenario_pool):
            success += 1

    logger.info("\n=== Done: %d personas, %d scenarios ===", len(personas), success)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=6)
    asyncio.run(main(parser.parse_args()))
