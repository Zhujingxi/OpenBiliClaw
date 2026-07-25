"""Repair recommendation copy that stored a serialized LLM payload.

A non-string field from the model used to be coerced with ``str()``, so a
nested batch answer was persisted as its Python repr and rendered to users as
card copy (``[{'expression': ..., 'topic_label': ...}]``).

Blanking the columns is not enough. The pool-copy backfill query skips any
bvid that already has a recommendation, so a blanked already-recommended row
never gets regenerated and the card simply renders empty instead. Rows without
a recommendation are cleared and left to the pipeline; rows with one get a
deterministic fallback written in the same transaction, which keeps the
recommendation id, timestamps, and feedback intact.

Defaults to a dry run. Pass --apply to write, and take a backup first.
"""

from __future__ import annotations

import argparse
import ast
import json
import sqlite3
import sys
from pathlib import Path

_PROBE_MAX_CHARS = 512
_MARKERS = ("expression", "topic_label", "reason", "topic_group")

_TARGET_COLUMNS = (
    ("content_cache", "pool_expression", "bvid"),
    ("content_cache", "pool_topic_label", "bvid"),
    ("content_cache", "delight_reason", "bvid"),
    ("recommendations", "expression", "bvid"),
)


def looks_like_serialized_payload(value: object) -> bool:
    """True when the stored text is a serialized dict/list of LLM fields.

    Substring matching on ``'topic_label'`` would flag normal copy that merely
    discusses the field, and miss JSON-quoted or whitespace-prefixed variants.
    Parsing is what separates "text about a payload" from "a payload".
    """
    if not isinstance(value, str):
        return False
    probe = value.strip()[:_PROBE_MAX_CHARS]
    if not probe.startswith(("{", "[")):
        return False
    for parse in (json.loads, ast.literal_eval):
        try:
            parsed = parse(probe)
        except (ValueError, SyntaxError, TypeError, RecursionError, MemoryError):
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if isinstance(item, dict) and any(marker in item for marker in _MARKERS):
                return True
    return False


def _fallback_expression(title: str, style_key: str) -> str:
    """Mirror the engine's deterministic fallback copy.

    Deliberately does not reuse any text parsed out of the poisoned value —
    the first entry of a nested batch usually belongs to a different video, so
    recovering from it would silently attach the wrong reason.
    """
    name = title or "这条内容"
    by_style = {
        "deep_focus": f"《{name}》偏需要认真看进去，但会把结构和原理讲清楚。",
        "quick_scan": f"《{name}》适合快速抓重点，先把发生了什么和关键变化过一遍。",
        "hands_on": f"《{name}》偏能照着用的实操内容，不只是概念。",
        "decision_support": f"《{name}》适合用来做判断，能帮你快速比较重点和取舍。",
        "story_immersion": f"《{name}》更像进入一个故事，信息会跟着人物和事件一起展开。",
    }
    return by_style.get(style_key, f"《{name}》和你最近关注的方向挺接得上。")


def find_polluted(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    """Group every polluted column value by bvid."""
    findings: dict[str, dict[str, object]] = {}
    for table, column, key in _TARGET_COLUMNS:
        try:
            rows = conn.execute(
                f"SELECT {key} AS k, {column} AS v FROM {table} "  # noqa: S608 - fixed identifiers
                f"WHERE COALESCE({column}, '') != ''"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            print(f"  ! skipping {table}.{column}: {exc}")
            continue
        for row in rows:
            if looks_like_serialized_payload(row["v"]):
                entry = findings.setdefault(str(row["k"]), {"columns": []})
                columns = entry["columns"]
                assert isinstance(columns, list)
                columns.append(f"{table}.{column}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    if not args.database.exists():
        print(f"database not found: {args.database}")
        return 1

    conn = sqlite3.connect(args.database)
    conn.row_factory = sqlite3.Row

    findings = find_polluted(conn)
    if not findings:
        print("no serialized payloads found — nothing to do")
        return 0

    print(f"found {len(findings)} polluted content row(s):\n")
    plan: list[tuple[str, bool, str]] = []
    for bvid, entry in findings.items():
        meta = conn.execute(
            "SELECT title, style_key FROM content_cache WHERE bvid = ?", (bvid,)
        ).fetchone()
        recommended = conn.execute(
            "SELECT 1 FROM recommendations WHERE bvid = ? LIMIT 1", (bvid,)
        ).fetchone()
        title = str(meta["title"]) if meta else ""
        style_key = str(meta["style_key"]) if meta else ""
        fallback = _fallback_expression(title, style_key)
        plan.append((bvid, bool(recommended), fallback))
        columns = entry["columns"]
        assert isinstance(columns, list)
        print(f"  {bvid}  columns={','.join(columns)}")
        print(f"    recommended={'yes' if recommended else 'no'}")
        print(f"    action={'fallback copy' if recommended else 'clear for recompute'}")
        if recommended:
            print(f"    fallback={fallback}")
        print()

    if not args.apply:
        print("dry run — re-run with --apply to write (back up the database first)")
        return 0

    with conn:  # one transaction: partial repair would leave a mixed state
        for bvid, recommended, fallback in plan:
            if recommended:
                conn.execute(
                    "UPDATE content_cache SET pool_expression = ?, delight_reason = '' "
                    "WHERE bvid = ?",
                    (fallback, bvid),
                )
                conn.execute(
                    "UPDATE recommendations SET expression = ? WHERE bvid = ?",
                    (fallback, bvid),
                )
            else:
                conn.execute(
                    "UPDATE content_cache SET pool_expression = '', pool_topic_label = '', "
                    "delight_reason = '' WHERE bvid = ?",
                    (bvid,),
                )
            conn.execute(
                "UPDATE content_cache SET pool_topic_label = '' "
                "WHERE bvid = ? AND pool_topic_label LIKE '[%'",
                (bvid,),
            )

    remaining = find_polluted(conn)
    print(f"applied. remaining polluted rows: {len(remaining)}")
    return 0 if not remaining else 1


if __name__ == "__main__":
    sys.exit(main())
