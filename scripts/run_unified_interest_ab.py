"""Real-LLM A/B quality gate for the unified interest line (Wave B, Phase 2).

Runs the SAME feedback set through both interest-update paths and reports the
three gates from ``docs/plans/2026-07-27-unified-interest-line-spec.md`` §Phase 2:

  gate1  new-dislike superset — the unified line's newly added ``disliked_topics``
         must be a superset of the legacy batch's (negative feedback semantics
         must not be lost by the merge).
  gate2  top-10 interest-name Jaccard >= 0.8 — the existing interest picture must
         survive the input-shape change (compact feedback rows -> full signals).
  gate3  retraction non-amplification — the deliberate ``exclude -> discount``
         change for retractions must not produce a new dislike or push any
         interest weight UP.

Path A is the legacy ``_process_feedback_batch_if_needed_locked`` driven through
its own cursor. Path B builds ``SignalType.FEEDBACK`` signals with
``signal_from_feedback`` (NOT ``signals_from_events`` — that one never emits
FEEDBACK, so the batch would lose every feedback privilege) and drives the
INTEREST buffer consumption directly.

Isolation: every run gets its own ``shutil.copytree`` of the project root, so no
path can observe another's writes and the source root is never mutated —
including by ``--synthetic``, which appends its clearly-labelled rows to a copy.

Usage:
    OPENBILICLAW_PROJECT_ROOT=/path/to/isolated/root \\
        .venv/bin/python scripts/run_unified_interest_ab.py [--limit N] [--synthetic]

Exit codes: 0 all gates passed, 1 a gate failed, 2 bad input (sample too small,
missing root, ...).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

# Spec §Phase 2: "取真实库最近 N>=8 条 feedback 事件". Below this the Jaccard gate
# is dominated by sampling noise rather than by the input-shape change it exists
# to measure.
MIN_FEEDBACK_SAMPLE = 8
# Spec §Phase 2 gate 2. Not recalibrated here — a provider swap reopens it.
DEFAULT_JACCARD_FLOOR = 0.8
DEFAULT_TOP_N = 10
# Everything this script fabricates carries this prefix so a stray row in a copy
# is never mistaken for real user feedback.
SYNTHETIC_PREFIX = "[AB-SYNTHETIC]"

EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_BAD_INPUT = 2


# ---------------------------------------------------------------------------
# Pure comparison / gate logic (unit-tested with canned inputs, no LLM)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    """One quality gate's verdict plus the numbers behind it."""

    name: str
    passed: bool
    observed: str
    threshold: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "observed": self.observed,
            "threshold": self.threshold,
            "detail": self.detail,
        }


def _string_set(value: Any) -> set[str]:
    """Normalize a ``disliked_topics``-shaped value into a clean string set."""
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def new_dislikes(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    """Topics that appear in ``after``'s dislikes but not ``before``'s."""
    return _string_set(after.get("disliked_topics")) - _string_set(before.get("disliked_topics"))


def interest_weights(preference: dict[str, Any]) -> dict[str, float]:
    """``{interest name: weight}`` for a flat preference snapshot.

    Archived interests are excluded: a dislike-driven archive is the batch doing
    its job, not an interest that changed weight.
    """
    weights: dict[str, float] = {}
    raw = preference.get("interests")
    if not isinstance(raw, list):
        return weights
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name or str(item.get("state", "")).strip() == "archived":
            continue
        try:
            weights[name] = float(item.get("weight", 0.0))
        except (TypeError, ValueError):
            weights[name] = 0.0
    return weights


def top_interest_names(preference: dict[str, Any], top_n: int = DEFAULT_TOP_N) -> list[str]:
    """The ``top_n`` interest names by weight, heaviest first (name-stable ties)."""
    weights = interest_weights(preference)
    ordered = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in ordered[:top_n]]


def jaccard(left: set[str], right: set[str]) -> float:
    """Jaccard similarity; two empty sets are identical (1.0), not undefined."""
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def gate1_new_dislike_superset(
    *,
    legacy_before: dict[str, Any],
    legacy_after: dict[str, Any],
    unified_before: dict[str, Any],
    unified_after: dict[str, Any],
) -> GateResult:
    """Gate 1: B's newly added dislikes must cover every one A added."""
    legacy_new = new_dislikes(legacy_before, legacy_after)
    unified_new = new_dislikes(unified_before, unified_after)
    missing = legacy_new - unified_new
    return GateResult(
        name="gate1_new_dislike_superset",
        passed=not missing,
        observed=f"unified新增{len(unified_new)}条 ⊇ legacy新增{len(legacy_new)}条"
        + (f"；缺失 {sorted(missing)}" if missing else ""),
        threshold="unified_new ⊇ legacy_new",
        detail={
            "legacy_new_dislikes": sorted(legacy_new),
            "unified_new_dislikes": sorted(unified_new),
            "missing_from_unified": sorted(missing),
        },
    )


def gate2_top_interest_jaccard(
    *,
    legacy_after: dict[str, Any],
    unified_after: dict[str, Any],
    top_n: int = DEFAULT_TOP_N,
    floor: float = DEFAULT_JACCARD_FLOOR,
) -> GateResult:
    """Gate 2: the two paths' top-N interest names must overlap at >= ``floor``."""
    legacy_top = set(top_interest_names(legacy_after, top_n))
    unified_top = set(top_interest_names(unified_after, top_n))
    score = jaccard(legacy_top, unified_top)
    return GateResult(
        name="gate2_top_interest_jaccard",
        passed=score >= floor,
        observed=f"{score:.3f}",
        threshold=f">= {floor}",
        detail={
            "top_n": top_n,
            "legacy_top": sorted(legacy_top),
            "unified_top": sorted(unified_top),
            "only_in_legacy": sorted(legacy_top - unified_top),
            "only_in_unified": sorted(unified_top - legacy_top),
        },
    )


def gate3_retraction_not_amplified(
    *,
    baseline_after: dict[str, Any],
    retraction_after: dict[str, Any],
) -> GateResult:
    """Gate 3: adding one retraction to B must add no dislike and lift no weight.

    Both inputs are unified-line runs over the same feedback set; the second one
    also carries a synthetic retraction. The legacy batch dropped retractions
    entirely, so this is the one deliberate semantic change in the merge and it
    has to be shown to be non-amplifying in both directions that matter.
    """
    added_dislikes = new_dislikes(baseline_after, retraction_after)
    base_weights = interest_weights(baseline_after)
    retr_weights = interest_weights(retraction_after)
    raised = {
        name: [base_weights[name], weight]
        for name, weight in retr_weights.items()
        if name in base_weights and weight > base_weights[name]
    }
    introduced = sorted(set(retr_weights) - set(base_weights))
    passed = not added_dislikes and not raised and not introduced
    parts: list[str] = []
    if added_dislikes:
        parts.append(f"新增dislike {sorted(added_dislikes)}")
    if raised:
        parts.append(f"权重上调 {sorted(raised)}")
    if introduced:
        parts.append(f"新增兴趣 {introduced}")
    return GateResult(
        name="gate3_retraction_not_amplified",
        passed=passed,
        observed="；".join(parts) if parts else "无新增dislike、无权重上调",
        threshold="no new dislike AND no interest weight increase",
        detail={
            "added_dislikes": sorted(added_dislikes),
            "raised_weights": raised,
            "introduced_interests": introduced,
        },
    )


def evaluate_gates(
    *,
    legacy_before: dict[str, Any],
    legacy_after: dict[str, Any],
    unified_before: dict[str, Any],
    unified_after: dict[str, Any],
    unified_retraction_after: dict[str, Any],
    top_n: int = DEFAULT_TOP_N,
    floor: float = DEFAULT_JACCARD_FLOOR,
) -> list[GateResult]:
    """All three gates, in spec order."""
    return [
        gate1_new_dislike_superset(
            legacy_before=legacy_before,
            legacy_after=legacy_after,
            unified_before=unified_before,
            unified_after=unified_after,
        ),
        gate2_top_interest_jaccard(
            legacy_after=legacy_after,
            unified_after=unified_after,
            top_n=top_n,
            floor=floor,
        ),
        gate3_retraction_not_amplified(
            baseline_after=unified_after,
            retraction_after=unified_retraction_after,
        ),
    ]


def render_table(gates: list[GateResult]) -> str:
    """Human-readable gate table (the JSON summary is the machine contract)."""
    rows = [("门", "结果", "观测值", "门槛")]
    rows += [
        (gate.name, "PASS" if gate.passed else "FAIL", gate.observed, gate.threshold)
        for gate in gates
    ]
    widths = [max(len(str(row[i])) for row in rows) for i in range(4)]
    lines = []
    for index, row in enumerate(rows):
        lines.append("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
        if index == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def build_summary(
    *,
    gates: list[GateResult],
    sample_size: int,
    synthetic_added: int,
    source_root: str,
    baseline_commit: str,
) -> dict[str, Any]:
    """The machine-readable summary printed last on stdout."""
    return {
        "schema": "unified_interest_line_ab/1",
        "source_root": source_root,
        "baseline_commit": baseline_commit,
        "sample_size": sample_size,
        "synthetic_feedback_added": synthetic_added,
        "all_gates_passed": all(gate.passed for gate in gates),
        "gates": [gate.to_dict() for gate in gates],
    }


# ---------------------------------------------------------------------------
# Runner (real LLM; never executed by the unit tests)
# ---------------------------------------------------------------------------


def _git_head(root: Path) -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _clone_root(source: Path, destination: Path) -> Path:
    """A full copy of the project root — config, memory layers, and database."""
    shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=False)
    return destination


def _open_root(root: Path) -> tuple[Any, Any, Any]:
    """Build ``(config, memory, database)`` bound to ``root``."""
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(root)
    from openbiliclaw.config import load_config
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.storage.database import Database

    config = load_config()
    database = Database(config.data_path / "openbiliclaw.db")
    database.initialize()
    memory = MemoryManager(config.data_path, database=database)
    memory.initialize()
    return config, memory, database


def _build_engine(root: Path, *, unified: bool, threshold: int) -> Any:
    from openbiliclaw.llm.registry import build_llm_registry
    from openbiliclaw.soul.engine import SoulEngine

    config, memory, database = _open_root(root)
    return SoulEngine(
        llm=build_llm_registry(config),
        memory=memory,
        database=database,
        feedback_batch_threshold=threshold,
        unified_interest_line=unified,
    )


def _feedback_rows(memory: Any, *, after_event_id: int) -> list[dict[str, Any]]:
    """Deserialized feedback events past ``after_event_id``, oldest first."""
    from openbiliclaw.soul.engine import SoulEngine

    return [
        SoulEngine._deserialize_event(event)
        for event in memory.query_events_since(
            after_event_id=after_event_id,
            event_types=["feedback"],
        )
    ]


def _is_retraction(event: dict[str, Any]) -> bool:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return str(metadata.get("feedback_type") or "").strip().lower() == "retraction"


async def _append_synthetic_feedback(memory: Any, count: int) -> None:
    """Top the sample up with clearly-labelled synthetic rows (copy only)."""
    kinds = ["dislike", "like"]
    for index in range(count):
        await memory.propagate_event(
            {
                "event_type": "feedback",
                "title": f"{SYNTHETIC_PREFIX} 合成反馈样本 {index}",
                "metadata": {
                    "feedback_type": kinds[index % len(kinds)],
                    "feedback_note": f"{SYNTHETIC_PREFIX} A/B 样本补齐，非真实用户反馈",
                    "synthetic": True,
                },
            }
        )


async def _append_synthetic_retraction(memory: Any, title: str) -> None:
    """One synthetic retraction of an existing positive (gate 3's probe)."""
    await memory.propagate_event(
        {
            "event_type": "feedback",
            "title": title,
            "metadata": {
                "feedback_type": "retraction",
                "feedback_note": f"{SYNTHETIC_PREFIX} 门3 撤回探针",
                "synthetic": True,
            },
        }
    )


async def _run_legacy(
    root: Path, *, cursor: int, threshold: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Path A: the legacy batch, driven through its own cursor."""
    engine = _build_engine(root, unified=False, threshold=threshold)
    memory = engine._memory
    memory.save_feedback_state(
        {
            "last_processed_feedback_event_id": cursor,
            "last_feedback_reanalyzed_at": "",
        }
    )
    before = dict(memory.get_layer("preference").data)
    await engine._process_feedback_batch_if_needed_locked()
    await engine.wait_for_pending_edits()
    return before, dict(memory.get_layer("preference").data)


async def _run_unified(
    root: Path, rows: list[dict[str, Any]], *, threshold: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Path B: FEEDBACK signals through the pipeline's INTEREST buffer."""
    from openbiliclaw.soul.pipeline import signal_from_feedback

    engine = _build_engine(root, unified=True, threshold=threshold)
    memory = engine._memory
    before = dict(memory.get_layer("preference").data)
    signals: list[Any] = []
    for row in rows:
        metadata = row.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        signals.append(
            signal_from_feedback(
                str(metadata.get("feedback_type") or "").strip(),
                str(row.get("title") or ""),
                str(metadata.get("feedback_note") or ""),
            )
        )
    await engine.pipeline.ingest_batch(signals)
    # A quiet buffer can still be short of the priority threshold; force the
    # consumption the scheduler's shim would trigger.
    await engine.pipeline.tick()
    await engine.wait_for_pending_edits()
    return before, dict(memory.get_layer("preference").data)


async def _amain(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="how many of the most recent post-cursor feedback events to use (default: all)",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help=f"top the sample up to {MIN_FEEDBACK_SAMPLE} with clearly-labelled "
        "synthetic feedback appended to a COPY (never the source root)",
    )
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--jaccard-floor", type=float, default=DEFAULT_JACCARD_FLOOR)
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="do not delete the temporary run copies (for post-mortem)",
    )
    args = parser.parse_args(argv)

    raw_root = os.environ.get("OPENBILICLAW_PROJECT_ROOT", "").strip()
    if not raw_root:
        print(
            "OPENBILICLAW_PROJECT_ROOT must point at an ISOLATED copy of a real "
            "project root (never your live one).",
            file=sys.stderr,
        )
        return EXIT_BAD_INPUT
    source_root = Path(raw_root).expanduser().resolve()
    if not source_root.is_dir():
        print(f"OPENBILICLAW_PROJECT_ROOT does not exist: {source_root}", file=sys.stderr)
        return EXIT_BAD_INPUT

    workdir = Path(tempfile.mkdtemp(prefix="unified-interest-ab-"))
    try:
        # A staging copy holds the sample (plus any synthetic top-up) so the
        # source root is opened read-only exactly once and never written.
        sample_root = _clone_root(source_root, workdir / "sample")
        _, sample_memory, _ = _open_root(sample_root)
        cursor = int(sample_memory.load_feedback_state().get("last_processed_feedback_event_id", 0))
        rows = [row for row in _feedback_rows(sample_memory, after_event_id=cursor)]
        real_rows = [row for row in rows if not _is_retraction(row)]

        synthetic_added = 0
        if len(real_rows) < MIN_FEEDBACK_SAMPLE:
            if not args.synthetic:
                print(
                    f"Only {len(real_rows)} real feedback event(s) after cursor {cursor}; "
                    f"the gates need at least {MIN_FEEDBACK_SAMPLE}. Re-run with --synthetic "
                    "to top the sample up with clearly-labelled synthetic feedback (appended "
                    "to a copy, never to the source root).",
                    file=sys.stderr,
                )
                return EXIT_BAD_INPUT
            synthetic_added = MIN_FEEDBACK_SAMPLE - len(real_rows)
            await _append_synthetic_feedback(sample_memory, synthetic_added)
            rows = [row for row in _feedback_rows(sample_memory, after_event_id=cursor)]
            real_rows = [row for row in rows if not _is_retraction(row)]

        if args.limit and args.limit < len(real_rows):
            real_rows = real_rows[-args.limit :]
        # Path A consumes everything after its cursor, so line the cursor up
        # with the sample instead of hand-feeding it a different set.
        first_id = int(real_rows[0].get("id", 0))
        legacy_cursor = max(0, first_id - 1)
        # One retraction of the newest positive drives gate 3.
        retraction_title = str(real_rows[-1].get("title") or "")

        threshold = max(1, len(real_rows))
        print(
            f"样本：{len(real_rows)} 条反馈（合成补齐 {synthetic_added} 条），"
            f"游标 {legacy_cursor}，源根 {source_root}",
            file=sys.stderr,
        )

        legacy_before, legacy_after = await _run_legacy(
            _clone_root(sample_root, workdir / "legacy"),
            cursor=legacy_cursor,
            threshold=threshold,
        )
        unified_before, unified_after = await _run_unified(
            _clone_root(sample_root, workdir / "unified"),
            real_rows,
            threshold=threshold,
        )

        # Gate 3: the same unified run, plus one synthetic retraction. The
        # legacy path is run with it too (it must be inert there — the batch
        # drops retractions) so the report can show both sides of the change.
        retraction_root = _clone_root(sample_root, workdir / "unified-retraction")
        _, retraction_memory, _ = _open_root(retraction_root)
        await _append_synthetic_retraction(retraction_memory, retraction_title)
        retraction_rows = [
            *real_rows,
            *[
                row
                for row in _feedback_rows(retraction_memory, after_event_id=cursor)
                if _is_retraction(row)
            ],
        ]
        _, unified_retraction_after = await _run_unified(
            retraction_root, retraction_rows, threshold=threshold
        )

        gates = evaluate_gates(
            legacy_before=legacy_before,
            legacy_after=legacy_after,
            unified_before=unified_before,
            unified_after=unified_after,
            unified_retraction_after=unified_retraction_after,
            top_n=args.top_n,
            floor=args.jaccard_floor,
        )
        print(render_table(gates), file=sys.stderr)
        summary = build_summary(
            gates=gates,
            sample_size=len(real_rows),
            synthetic_added=synthetic_added,
            source_root=str(source_root),
            baseline_commit=_git_head(PROJECT_ROOT),
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK if summary["all_gates_passed"] else EXIT_GATE_FAILED
    finally:
        if args.keep_workdir:
            print(f"workdir kept at {workdir}", file=sys.stderr)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(list(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    raise SystemExit(main())
