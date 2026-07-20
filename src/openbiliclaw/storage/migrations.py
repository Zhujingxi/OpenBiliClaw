"""Data-driven additive column migrations (Phase 2A).

``ensure_columns`` is the single helper that backs the legacy
``Database._ensure_*_columns`` methods whose behavior is purely additive:
check ``PRAGMA table_info`` for missing columns, then issue
``ALTER TABLE ... ADD COLUMN`` for each missing one. The helper does NOT
introduce a schema-version ledger, does NOT change when migrations run
(they are still invoked lazily from the same call sites), and does NOT
alter transaction / lock timing — the caller's connection is used as-is.

Hard constraints (per the architecture plan, M-1 / Phase 2A):

* Table names and column names are validated against a static whitelist
  (``_ALLOWED_TABLES`` and ``_ALLOWED_COLUMNS``) before any identifier is
  interpolated into SQL. The helper never accepts arbitrary identifiers
  from untrusted callers.
* Only pure additive migrations belong here. Methods that couple column
  additions with backfills, dedup consolidation, or index changes stay
  hand-written in ``storage/database.py`` and are explicitly registered
  as exceptions in the refactor plan.
* No schema-version table is created. That is the explicitly deferred
  Phase 2B scope.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping

# Whitelist of table names the helper will touch. Anything else is a
# programming error and raises ``ValueError`` before any SQL is emitted.
_ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "content_cache",
        "discovery_candidates",
        "events",
        "llm_usage",
        "recommendations",
    }
)

# Whitelist of column names the helper will add. The set is derived from
# the union of every ``required_columns`` dict in the legacy
# ``_ensure_*_columns`` methods that are being migrated to this helper.
# New additive columns must be added here when their migration is
# introduced; arbitrary identifier injection is rejected.
_ALLOWED_COLUMNS: frozenset[str] = frozenset(
    {
        # events
        "inferred_satisfaction",
        "satisfaction_reason",
        # recommendations
        "feedback_type",
        "feedback_note",
        "feedback_at",
        # content_cache — runtime
        "last_scored_at",
        "notification_sent",
        "notified_at",
        "pool_status",
        "recommended_at",
        "source",
        # content_cache — relevance
        "relevance_score",
        "relevance_reason",
        "candidate_tier",
        # content_cache — topic
        "topic_key",
        "topic_group",
        "style_key",
        "franchise_key",
        # content_cache — pool copy
        "pool_expression",
        "pool_topic_label",
        # content_cache — delight
        "delight_score",
        "delight_reason",
        "delight_hook",
        "delight_notified",
        "delight_notified_at",
        # llm_usage
        "cached_input_tokens",
        "connection_id",
        "connection_type",
        "preset",
        "route_position",
        # discovery_candidates
        "score_threshold",
        "eval_attempts",
        "batch_eval_attempts",
        "claim_token",
        "body_text",
        "favorite_count",
        "collect_count",
        "comment_count",
        "share_count",
        "danmaku_count",
        "reply_count",
        "retweet_count",
        "bookmark_count",
        "published_at",
        "published_label",
        "source_keyword_id",
    }
)

# SQLite identifiers in this codebase are snake_case ASCII. The regex is
# a defense-in-depth complement to the explicit whitelists above — even
# if a future refactor broadens the whitelists, identifiers still must
# match this shape before they can reach ``ALTER TABLE``.
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_identifier(value: str, *, kind: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(
            f"ensure_columns: {kind} {value!r} is not in the static whitelist; "
            "add it explicitly when introducing a new additive migration"
        )
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(
            f"ensure_columns: {kind} {value!r} does not match the identifier shape; "
            "refusing to interpolate into SQL"
        )


def ensure_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: Mapping[str, str],
) -> list[str]:
    """Additively ensure ``columns`` exist on ``table``.

    For each ``(name, type_clause)`` pair in ``columns``:

    * If ``name`` already appears in ``PRAGMA table_info(table)``, skip it.
    * Otherwise issue ``ALTER TABLE <table> ADD COLUMN <name> <type_clause>``.

    Returns the list of column names that were actually added (preserving
    the iteration order of ``columns``). An empty return value means the
    call was a no-op — the second invocation on an already-migrated
    database always returns ``[]``.

    The caller retains full control over transactions: this function does
    NOT call ``conn.commit()`` or ``conn.rollback()``. The legacy lazy
    call sites in ``Database.initialize()`` rely on the surrounding
    transaction scope, and that timing is preserved here.

    ``type_clause`` is trusted DDL text (e.g. ``"TEXT NOT NULL DEFAULT ''"``
    or ``"INTEGER"``). It is NOT user input — it comes from the static
    ``required_columns`` dict in each ``_ensure_*_columns`` method. Only
    the *identifiers* (table and column names) are whitelist-validated
    here; the type clause is passed through verbatim, matching the
    behavior of the legacy hand-written methods.
    """
    _validate_identifier(table, kind="table", allowed=_ALLOWED_TABLES)
    for name in columns:
        _validate_identifier(name, kind="column", allowed=_ALLOWED_COLUMNS)

    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    added: list[str] = []
    for name, type_clause in columns.items():
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {type_clause}")
        added.append(name)
    return added


__all__ = ["ensure_columns"]
