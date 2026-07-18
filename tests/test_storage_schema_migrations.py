"""Storage schema migration idempotency and behavior tests (Phase 0).

Verifies that ``Database.initialize()`` plus the additive
``_ensure_*_columns`` migrations converge to the same schema regardless of
starting point: empty, legacy, current, or partially migrated. Running the
full initialization twice must be a no-op the second time.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

import pytest

from openbiliclaw.storage.database import Database
from tests.fixtures.storage_schema import (
    _column_names,
    make_current_db,
    make_empty_db,
    make_partial_db,
)


def _snapshot_schema(conn: sqlite3.Connection) -> dict[str, list[str]]:
    tables = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    return {
        table: sorted(
            str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
        )
        for table in sorted(tables)
    }


def test_initialize_on_empty_db_converges_to_current_schema(tmp_path: Path) -> None:
    """Fresh database reaches the same schema as the reference current DB."""
    fresh_db = Database(tmp_path / "fresh.db")
    fresh_db.initialize()
    fresh_schema = _snapshot_schema(fresh_db.conn)

    current = make_current_db(tmp_path)
    current_schema = _snapshot_schema(current)

    # Both DBs go through Database.initialize() so they must agree on the
    # exact set of tables and columns.
    assert fresh_schema == current_schema


def test_ensure_columns_idempotent_on_current_db(tmp_path: Path) -> None:
    """Running the additive column migrations a second time is a no-op."""
    conn = make_current_db(tmp_path)
    before = _snapshot_schema(conn)

    db = Database.__new__(Database)
    db._db_path = Path(":memory:")
    db._conn = conn
    db._admission_min_score = 0.0

    # Re-run the same column-ensure entry points initialize() calls.
    db._ensure_event_satisfaction_columns()
    db._ensure_recommendation_feedback_columns()
    db._ensure_content_cache_runtime_columns()
    db._ensure_content_cache_relevance_columns()
    db._ensure_content_cache_topic_columns()
    db._ensure_content_cache_pool_copy_columns()
    db._ensure_content_cache_delight_columns()
    db._ensure_content_cache_multisource_columns()
    db._ensure_llm_usage_columns()
    db._ensure_discovery_candidate_columns()

    after = _snapshot_schema(conn)
    assert after == before, "re-running _ensure_*_columns changed the schema"


def test_ensure_columns_fills_missing_columns_on_partial_db(tmp_path: Path) -> None:
    """A half-migrated DB (missing some additive columns) is repaired."""
    conn = make_partial_db(
        tmp_path, "content_cache", missing_columns={"delight_score", "delight_reason"}
    )
    cols_before = _column_names(conn, "content_cache")
    assert "delight_score" not in cols_before
    assert "delight_reason" not in cols_before

    db = Database.__new__(Database)
    db._db_path = Path(":memory:")
    db._conn = conn
    db._admission_min_score = 0.0
    db._ensure_content_cache_delight_columns()

    cols_after = _column_names(conn, "content_cache")
    assert "delight_score" in cols_after
    assert "delight_reason" in cols_after


@pytest.mark.parametrize(
    "method_name,table,expected_columns",
    [
        (
            "_ensure_event_satisfaction_columns",
            "events",
            {"inferred_satisfaction", "satisfaction_reason"},
        ),
        (
            "_ensure_recommendation_feedback_columns",
            "recommendations",
            {"feedback_type", "feedback_note", "feedback_at"},
        ),
        (
            "_ensure_content_cache_delight_columns",
            "content_cache",
            {
                "delight_score",
                "delight_reason",
                "delight_hook",
                "delight_notified",
                "delight_notified_at",
            },
        ),
        (
            "_ensure_llm_usage_columns",
            "llm_usage",
            {
                "cached_input_tokens",
                "connection_id",
                "connection_type",
                "preset",
                "route_position",
            },
        ),
    ],
)
def test_each_migration_is_idempotent(
    tmp_path: Path, method_name: str, table: str, expected_columns: set[str]
) -> None:
    """Each additive column migration runs cleanly twice on a current DB."""
    conn = make_current_db(tmp_path)
    db = Database.__new__(Database)
    db._db_path = Path(":memory:")
    db._conn = conn
    db._admission_min_score = 0.0

    method = getattr(db, method_name)
    method()
    cols_after_first = _column_names(conn, table)
    assert expected_columns <= cols_after_first, (
        f"{method_name} did not produce expected columns on {table}"
    )

    # Second invocation must be a no-op (no exception, no schema change).
    method()
    cols_after_second = _column_names(conn, table)
    assert cols_after_second == cols_after_first


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    """Running Database.initialize() twice on the same path is safe."""
    db = Database(tmp_path / "repeat.db")
    db.initialize()
    first_schema = _snapshot_schema(db.conn)
    # Close the existing connection so the second initialize() can reopen it.
    db.conn.close()
    db.initialize()
    second_schema = _snapshot_schema(db.conn)
    assert first_schema == second_schema


def test_empty_raw_connection_has_no_tables() -> None:
    """Sanity check on the empty fixture itself."""
    conn = make_empty_db()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    assert tables == []
