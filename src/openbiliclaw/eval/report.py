"""Report generation for evaluation results.

Renders training curves, deviation summaries, and optimization logs
for both human review and automated processing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def render_training_summary(result: dict[str, Any]) -> str:
    """Render optimization result as a human-readable table."""
    lines: list[str] = []
    lines.append("═══ 训练报告 ═══")
    lines.append("")
    lines.append(f"{'Epoch':>5}  {'Train':>6}  {'Val':>6}  {'Changed':<40}  {'Explore?'}")
    lines.append("─" * 80)

    for h in result.get("history", []):
        changed = ", ".join(
            h.get("params_changed", h.get("summary", ["-"])[:1])
            if isinstance(h.get("params_changed"), list)
            else [str(h.get("summary", "-"))[:40]]
        )
        if len(changed) > 40:
            changed = changed[:37] + "..."
        explore = h.get("action", "Yes" if h.get("exploration") else "No")
        accepted = "✓" if h.get("accepted") else "✗"
        val_mean = h.get("val_mean", h.get("train_mean", 0))
        lines.append(
            f"{h['epoch']:>5}  {h['train_mean']:>6.3f}  {val_mean:>6.3f}"
            f"  {changed:<40}  {explore} {accepted}"
        )

    lines.append("")
    lines.append(
        f"Best val score: {result.get('best_score', 0):.3f} at epoch {result.get('best_epoch', 0)}"
    )
    lines.append(f"Stop reason: {result.get('stop_reason', 'unknown')}")
    lines.append(f"Epochs run: {result.get('epochs_run', 0)}")

    # Final attributions
    attrs = result.get("final_attributions", [])
    if attrs:
        lines.append("")
        lines.append("═══ 最大偏差归因 ═══")
        for a in attrs[:5]:
            lines.append(f"  {a}")

    return "\n".join(lines)


def save_training_curve(result: dict[str, Any], data_dir: Path) -> Path:
    """Save training curve data as JSON for visualization."""
    eval_dir = data_dir / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    path = eval_dir / "training_curve.json"

    curve: dict[str, list[object]] = {
        "epochs": [],
        "train_scores": [],
        "val_scores": [],
        "accepted": [],
    }
    for h in result.get("history", []):
        curve["epochs"].append(h["epoch"])
        curve["train_scores"].append(h["train_mean"])
        curve["val_scores"].append(h["val_mean"])
        curve["accepted"].append(h.get("accepted", False))

    with open(path, "w", encoding="utf-8") as f:
        json.dump(curve, f, ensure_ascii=False, indent=2)

    return path


def render_eval_report(report: dict[str, Any]) -> str:
    """Render a single EvalReport as human-readable text."""
    lines: list[str] = []
    lines.append(f"═══ 评估报告 (score: {report.get('overall_score', 0):.3f}) ═══")
    lines.append("")

    for ls in report.get("layer_scores", []):
        lines.append(f"  [{ls['layer']}] score: {ls['score']:.3f}")
        for fs in ls.get("field_scores", []):
            severity = fs.get("severity", "")
            icon = {"correct": "✅", "partial": "⚠️", "wrong": "❌", "missing": "💀"}.get(
                severity, "?"
            )
            dev = fs.get("deviation", "")
            dev_text = f" — {dev}" if dev else ""
            lines.append(f"    {icon} {fs['field']}: {fs['score']:.2f}{dev_text}")

    worst = report.get("worst_fields", [])
    if worst:
        lines.append("")
        lines.append("  最大偏差:")
        for f in worst[:3]:
            dev = f.get("deviation", "")
            lines.append(f"    {f['layer']}.{f['field']}: {f['score']:.2f} — {dev}")

    return "\n".join(lines)


def render_speculation_report(report: dict[str, Any]) -> str:
    """Render a SpeculationEvalReport as human-readable text."""
    lines: list[str] = []
    lines.append(f"═══ 推测兴趣评估 (score: {report.get('overall_score', 0):.3f}) ═══")
    lines.append("")
    lines.append(
        f"  合理性: {report.get('mean_plausibility', 0):.3f}  "
        f"新颖性: {report.get('mean_novelty', 0):.3f}  "
        f"具体性: {report.get('mean_specificity', 0):.3f}"
    )
    lines.append(
        f"  确认率: {report.get('confirmation_rate', 0):.3f}  "
        f"非幻觉: {report.get('mean_no_hallucination', 0):.3f}"
    )

    for ss in report.get("speculation_scores", []):
        icon = "✅" if ss.get("overall", 0) >= 0.7 else "⚠️" if ss.get("overall", 0) >= 0.4 else "❌"
        lines.append(f"\n  {icon} {ss.get('domain', '?')} ({ss.get('overall', 0):.2f})")
        lines.append(
            f"    合理={ss.get('plausibility', 0):.2f} "
            f"新颖={ss.get('novelty', 0):.2f} "
            f"具体={ss.get('specificity', 0):.2f} "
            f"非幻觉={ss.get('no_hallucination', 0):.2f}"
        )

    return "\n".join(lines)


def render_speculation_training_summary(result: dict[str, Any]) -> str:
    """Render speculation auto-optimize result as a summary table."""
    lines: list[str] = []
    lines.append("═══ 推测兴趣优化报告 ═══")
    lines.append("")
    lines.append(f"{'Epoch':>5}  {'Score':>6}  {'ConfRate':>8}  {'Action':<10}  {'Accepted'}")
    lines.append("─" * 60)

    for h in result.get("history", []):
        accepted = "✓" if h.get("accepted") else "✗"
        action = h.get("action", "?")
        lines.append(
            f"{h.get('epoch', 0):>5}  {h.get('train_mean', 0):>6.3f}  "
            f"{h.get('conf_rate', 0):>8.3f}  {action:<10}  {accepted}"
        )

    lines.append("")
    lines.append(
        f"Best score: {result.get('best_score', 0):.3f} at epoch {result.get('best_epoch', 0)}"
    )
    lines.append(f"Stop reason: {result.get('stop_reason', 'unknown')}")
    return "\n".join(lines)
