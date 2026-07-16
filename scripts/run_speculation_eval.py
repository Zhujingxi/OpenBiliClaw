"""Run speculative interest generation from real profile for human evaluation.

Loads the current user profile, generates speculative interests via LLM,
displays each speculation for human review, and logs all artifacts.

Usage:
    .venv/bin/python scripts/run_speculation_eval.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


async def main() -> None:
    from openbiliclaw.config import load_config
    from openbiliclaw.eval.report import render_speculation_report
    from openbiliclaw.eval.run_logger import RunLogger
    from openbiliclaw.eval.speculation_evaluator import SpeculationEvaluator
    from openbiliclaw.llm.prompts import build_speculation_generation_prompt
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.profile import OnionProfile
    from openbiliclaw.soul.speculator import SpeculativeInterest
    from _model_runtime import build_script_model_bundle

    cfg = load_config()
    data_dir = cfg.data_path

    rl = RunLogger(task="speculation_human", data_dir=data_dir)

    print("=" * 60)
    print("推测兴趣生成 — 人工评测")
    print(f"日志目录: {rl.run_dir}")
    print("=" * 60)

    # --- 1. Load existing profile ---
    input_step = rl.step("input")

    memory = MemoryManager(data_dir)
    memory.initialize()

    soul_data = memory.get_layer("soul").data
    if not soul_data:
        print("\n❌ 未找到已有画像，请先运行 init 生成画像")
        return

    profile = OnionProfile.from_dict(soul_data)
    input_step.save_json("profile.json", soul_data)
    print(f"\n✅ 已加载画像: {profile.core.core_traits[:2]}")

    # Collect confirmed interest domains
    confirmed_domains = [d.domain for d in profile.interest.likes]
    print(f"  已确认兴趣: {len(confirmed_domains)} 个领域")

    # Load existing speculative state (if any)
    spec_state_path = data_dir / "memory" / "speculative_state.json"
    existing_specs: list[str] = []
    cooldown_domains: list[str] = []
    if spec_state_path.exists():
        with open(spec_state_path, encoding="utf-8") as f:
            state_data = json.load(f)
        existing_specs = [s.get("domain", "") for s in state_data.get("active", [])]
        cooldown_domains = [c.get("domain", "") for c in state_data.get("cooldown", [])]
        if existing_specs:
            print(f"  活跃推测: {existing_specs}")
        if cooldown_domains:
            print(f"  冷却中: {cooldown_domains}")

    # --- 2. Generate speculations ---
    gen_step = rl.step("generation")

    profile_ctx = profile.to_llm_context()
    prompt_messages = build_speculation_generation_prompt(
        profile_summary=profile_ctx,
        existing_speculations=existing_specs,
        cooldown_domains=cooldown_domains,
        confirmed_domains=confirmed_domains,
        count=5,
    )
    gen_step.save_prompt(prompt_messages)

    print("\n[1/3] 生成推测兴趣...")
    model_bundle = build_script_model_bundle(cfg, memory)
    response = await model_bundle.chat_route.complete(
        prompt_messages,
        temperature=0.7,
        max_tokens=4096,
    )

    raw_text = response.content if hasattr(response, "content") else str(response)
    gen_step.save_text("response.txt", raw_text)

    # Parse speculations
    from openbiliclaw.eval.agents import _extract_json

    try:
        parsed = _extract_json(raw_text)
        raw_specs = parsed.get("speculations", [])
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"  ❌ JSON 解析失败: {exc}")
        return

    speculations = [
        SpeculativeInterest.from_dict({
            **s,
            "created_at": "",
            "status": "active",
        })
        for s in raw_specs
        if isinstance(s, dict) and s.get("domain")
    ]
    gen_step.save_json("speculations.json", [s.to_dict() for s in speculations])
    print(f"  ✅ 生成 {len(speculations)} 条推测")

    # --- 3. Display for human review ---
    display_step = rl.step("display")
    output_lines: list[str] = []

    print(f"\n{'=' * 60}")
    print("推测兴趣生成完成 — 请逐条评测")
    print(f"{'=' * 60}")

    human_feedback: dict[str, dict[str, float]] = {}

    for idx, spec in enumerate(speculations, 1):
        header = f"\n━━━ 推测 {idx}/{len(speculations)} ━━━"
        print(header)
        output_lines.append(header)

        info = [
            f"  领域: {spec.domain}",
            f"  分类: {spec.category}",
            f"  推理: {spec.reason}",
            f"  置信度: {spec.confidence:.2f}",
        ]
        for line in info:
            print(line)
            output_lines.append(line)

        print("\n  请评分 (0-1, 直接回车跳过使用默认 0.5):")
        try:
            p_raw = input("    合理性 (心理桥接是否说得通): ").strip()
            n_raw = input("    新颖性 (不太明显也不太离谱): ").strip()
            s_raw = input("    可操作性 (能在B站找到此类内容): ").strip()
            r_raw = input("    共鸣度 (你自己会不会想看): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  跳过剩余评测")
            break

        human_feedback[spec.domain] = {
            "plausibility": float(p_raw) if p_raw else 0.5,
            "novelty": float(n_raw) if n_raw else 0.5,
            "specificity": float(s_raw) if s_raw else 0.5,
            "persona_resonance": float(r_raw) if r_raw else 0.5,
        }

    display_step.save_text("output.txt", "\n".join(output_lines))
    display_step.save_json("human_feedback.json", human_feedback)

    # --- 4. Evaluate ---
    eval_step = rl.step("eval")

    evaluator = SpeculationEvaluator()
    report = await evaluator.evaluate_with_human(speculations, human_feedback)
    eval_step.save_json("eval_report.json", report.to_dict())

    print(f"\n{render_speculation_report(report.to_dict())}")

    # --- Finish ---
    summary_path = rl.finish(
        speculation_count=len(speculations),
        feedback_count=len(human_feedback),
        overall_score=report.overall_score,
    )
    print(f"\n{'=' * 60}")
    print(f"完整日志: {rl.run_dir}")
    print(f"摘要: {summary_path}")
    print("请评测后告诉我需要修改推测生成 prompt 的地方")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
