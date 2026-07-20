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
    make_legacy_conn,
    make_partial_db,
)


def _make_bare_db(conn: sqlite3.Connection) -> Database:
    """A Database shell whose methods run against a raw (legacy) connection.

    ``Database.__new__`` skips ``__init__`` so no production DDL runs — the
    ``_ensure_*_columns`` methods then operate on exactly the schema the
    fixture created, letting us exercise the additive migrations against
    pre-migration (legacy) and half-migrated (partial) shapes directly.
    """
    db = Database.__new__(Database)
    db._db_path = Path(":memory:")
    db._conn = conn
    db._admission_min_score = 0.0
    return db


def _snapshot_schema(conn: sqlite3.Connection) -> dict[str, list[str]]:
    tables = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    return {
        table: sorted(str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall())
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
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert tables == []


# --- Phase 2A: ensure_columns helper ---


def test_ensure_columns_adds_only_missing(tmp_path: Path) -> None:
    """Helper adds missing columns and returns the added names in order."""
    from openbiliclaw.storage.migrations import ensure_columns

    conn = make_partial_db(
        tmp_path, "content_cache", missing_columns={"delight_score", "delight_reason"}
    )
    added = ensure_columns(
        conn,
        "content_cache",
        {
            "delight_score": "REAL DEFAULT 0.0",
            "delight_reason": "TEXT DEFAULT ''",
            "delight_hook": "TEXT DEFAULT ''",  # already present
        },
    )
    assert added == ["delight_score", "delight_reason"]
    cols = _column_names(conn, "content_cache")
    assert {"delight_score", "delight_reason", "delight_hook"} <= cols

    # Second call is a no-op.
    assert (
        ensure_columns(
            conn,
            "content_cache",
            {
                "delight_score": "REAL DEFAULT 0.0",
                "delight_reason": "TEXT DEFAULT ''",
            },
        )
        == []
    )


def test_ensure_columns_rejects_non_whitelisted_table() -> None:
    """Refuses to touch a table that is not in the static whitelist."""
    import sqlite3 as _sqlite3

    from openbiliclaw.storage.migrations import ensure_columns

    conn = _sqlite3.connect(":memory:")
    conn.row_factory = _sqlite3.Row
    conn.execute("CREATE TABLE arbitrary (id INTEGER)")
    with pytest.raises(ValueError, match="not in the static whitelist"):
        ensure_columns(conn, "arbitrary", {"x": "TEXT"})


def test_ensure_columns_rejects_non_whitelisted_column(tmp_path: Path) -> None:
    """Refuses to add a column name that is not in the static whitelist."""
    from openbiliclaw.storage.migrations import ensure_columns

    conn = make_current_db(tmp_path)
    with pytest.raises(ValueError, match="not in the static whitelist"):
        ensure_columns(conn, "events", {"attacker_controlled": "TEXT"})


def test_ensure_columns_rejects_identifier_shape_violation(tmp_path: Path) -> None:
    """Defense-in-depth: even a whitelisted name must match snake_case ASCII."""
    from openbiliclaw.storage import migrations

    conn = make_current_db(tmp_path)
    # Monkey-patch a bad name into the whitelist to prove the regex still bites.
    bad = "events; DROP TABLE events--"
    original = migrations._ALLOWED_TABLES
    migrations._ALLOWED_TABLES = original | {bad}  # type: ignore[attr-defined]
    try:
        with pytest.raises(ValueError, match="identifier shape"):
            migrations.ensure_columns(conn, bad, {"inferred_satisfaction": "TEXT"})
    finally:
        migrations._ALLOWED_TABLES = original  # type: ignore[attr-defined]


# --- Review-87 finding 5: legacy + per-method partial-schema coverage ---
#
# The nine ensure_columns-backed migrations must converge a database to the
# current schema from EVERY starting point the production code can
# encounter: a pre-migration legacy shape, a crash-mid-migration partial
# shape, the current shape (no-op), and the empty shape (covered by
# test_initialize_on_empty_db_converges_to_current_schema above). The
# production conversions are behavior-preserving only if each method adds
# exactly its declared columns with exactly the declared defaults — so we
# compare the full PRAGMA table_info rows, not just column names.

# (method_name, table, expected added columns) for all nine converted
# methods. _ensure_content_cache_multisource_columns and
# _ensure_content_identity_columns stay hand-written (backfill /
# consolidation) and are covered separately below.
_CONVERTED_METHODS: list[tuple[str, str, set[str]]] = [
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
        "_ensure_content_cache_runtime_columns",
        "content_cache",
        {
            "last_scored_at",
            "notification_sent",
            "notified_at",
            "pool_status",
            "recommended_at",
            "feedback_type",
            "feedback_at",
            "source",
        },
    ),
    (
        "_ensure_content_cache_relevance_columns",
        "content_cache",
        {"relevance_score", "relevance_reason", "candidate_tier"},
    ),
    (
        "_ensure_content_cache_topic_columns",
        "content_cache",
        {"topic_key", "topic_group", "style_key", "franchise_key"},
    ),
    (
        "_ensure_content_cache_pool_copy_columns",
        "content_cache",
        {"pool_expression", "pool_topic_label"},
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
        {"cached_input_tokens", "connection_id", "connection_type", "preset", "route_position"},
    ),
    (
        "_ensure_discovery_candidate_columns",
        "discovery_candidates",
        {
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
        },
    ),
]

# Non-content_cache converted methods only. The five content_cache column
# groups are exercised by dedicated tests below
# (test_partial_db_repairs_each_content_cache_column_group,
# test_ensure_columns_does_not_commit_or_rollback_content_cache), so tests
# scoped to the non-content tables parametrize over THIS set instead of
# collecting content_cache rows only to pytest.skip() them at runtime.
# Runtime skips mint new skip node IDs that the quality-baseline comparator
# (scripts/check_quality_baseline.py) must tolerate; static filtering at
# collection time produces zero skip records for redundant coverage
# (review-t_e03bfeff run 192 P1).
_NON_CONTENT_CONVERTED_METHODS: list[tuple[str, str, set[str]]] = [
    method for method in _CONVERTED_METHODS if method[1] != "content_cache"
]


def _pragma_rows(conn: sqlite3.Connection, table: str) -> dict[str, tuple]:
    """Full PRAGMA table_info rows keyed by column name (cid, name, type,
    notnull, dflt_value, pk) — comparing the whole row locks the declared
    defaults, not just the column names."""
    return {str(r[1]): tuple(r) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


@pytest.mark.parametrize("method_name,table,expected_columns", _CONVERTED_METHODS)
def test_converted_method_repairs_legacy_schema(
    method_name: str, table: str, expected_columns: set[str]
) -> None:
    """Legacy shape -> method -> columns present with production defaults.

    Runs each converted migration against the pre-migration legacy fixture
    (make_legacy_conn) TWICE: the first run must add exactly the expected
    columns, the second must be a no-op, and the resulting PRAGMA rows
    (type / notnull / default) must match the production-current schema.
    """
    conn = make_legacy_conn(tables=[table])
    cols_before = _column_names(conn, table)
    assert not (expected_columns & cols_before), (
        f"legacy fixture for {table} unexpectedly already has migrated columns"
    )

    db = _make_bare_db(conn)
    method = getattr(db, method_name)

    method()
    first = _column_names(conn, table)
    assert expected_columns <= first, f"{method_name} missed columns on legacy {table}"

    # Second invocation is a no-op.
    method()
    second = _column_names(conn, table)
    assert first == second


@pytest.mark.parametrize("method_name,table,expected_columns", _CONVERTED_METHODS)
def test_converted_method_matches_production_defaults(
    tmp_path: Path, method_name: str, table: str, expected_columns: set[str]
) -> None:
    """The columns added by each converted method must match the production
    schema shape. SQLite forbids non-constant defaults (e.g.
    ``DEFAULT CURRENT_TIMESTAMP``) in ``ALTER TABLE ... ADD COLUMN``, so a
    migrated legacy table legitimately reports ``dflt_value=None`` where a
    fresh ``CREATE TABLE`` reports the constant default. We therefore
    compare the migrated legacy table against the production
    ``Database.initialize()`` schema on (type, notnull, pk) exactly, and on
    ``dflt_value`` up to the ADD COLUMN constant-default restriction:
    a production non-constant default must surface as NULL after a legacy
    migration, while every constant default (numbers, quoted strings) must
    be preserved byte-identically."""
    legacy = make_legacy_conn(tables=[table])
    db = _make_bare_db(legacy)
    getattr(db, method_name)()
    migrated_rows = _pragma_rows(legacy, table)

    current = make_current_db(tmp_path)
    production_rows = _pragma_rows(current, table)

    non_constant_defaults = {"CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME"}
    for column in expected_columns:
        assert column in migrated_rows, f"{method_name} did not add {column}"
        assert column in production_rows, f"production schema lacks {column}"
        # (cid, name, type, notnull, dflt_value, pk)
        m_type, m_notnull, m_dflt, m_pk = migrated_rows[column][2:]
        p_type, p_notnull, p_dflt, p_pk = production_rows[column][2:]
        assert (m_type, m_notnull, m_pk) == (p_type, p_notnull, p_pk), (
            f"{table}.{column} type/notnull/pk drift: "
            f"migrated=({m_type},{m_notnull},{m_pk}) "
            f"production=({p_type},{p_notnull},{p_pk})"
        )
        if p_dflt in non_constant_defaults:
            # Unrepresentable in ADD COLUMN — NULL on migrated legacy rows
            # is the documented production behavior for pre-migration DBs.
            assert m_dflt is None, (
                f"{table}.{column}: expected NULL default after legacy migration "
                f"(SQLite cannot ADD COLUMN with {p_dflt}), got {m_dflt!r}"
            )
        else:
            assert m_dflt == p_dflt, (
                f"{table}.{column} default drift: migrated={m_dflt!r} production={p_dflt!r}"
            )


def test_partial_db_repairs_each_content_cache_column_group(tmp_path: Path) -> None:
    """Crash-mid-migration shape: every content_cache column group repairs
    independently when its own columns are the ones missing."""
    groups: dict[str, tuple[str, set[str]]] = {
        "runtime": (
            "_ensure_content_cache_runtime_columns",
            {"last_scored_at", "notification_sent", "pool_status"},
        ),
        "relevance": (
            "_ensure_content_cache_relevance_columns",
            {"relevance_score", "relevance_reason", "candidate_tier"},
        ),
        "topic": ("_ensure_content_cache_topic_columns", {"topic_key", "topic_group"}),
        "pool_copy": (
            "_ensure_content_cache_pool_copy_columns",
            {"pool_expression", "pool_topic_label"},
        ),
        "delight": (
            "_ensure_content_cache_delight_columns",
            {"delight_score", "delight_reason"},
        ),
    }
    for label, (method_name, missing) in groups.items():
        conn = make_partial_db(tmp_path / label, "content_cache", missing_columns=missing)
        before = _column_names(conn, "content_cache")
        assert not (missing & before), f"partial fixture ({label}) unexpectedly repaired"

        db = _make_bare_db(conn)
        getattr(db, method_name)()
        after_first = _column_names(conn, "content_cache")
        assert missing <= after_first, f"{method_name} did not repair {label}"

        getattr(db, method_name)()
        assert _column_names(conn, "content_cache") == after_first


@pytest.mark.parametrize("method_name,table,expected_columns", _NON_CONTENT_CONVERTED_METHODS)
def test_partial_db_repairs_each_non_content_table(
    tmp_path: Path, method_name: str, table: str, expected_columns: set[str]
) -> None:
    """Crash-mid-migration shape for the NON-content_cache tables (events,
    recommendations, llm_usage, discovery_candidates): each converted method
    must repair its own missing columns from a partial fixture, and the
    second invocation must be a no-op (review-t_cce76b68 F6).
    """
    conn = make_partial_db(tmp_path / table, table, missing_columns=expected_columns)
    before = _column_names(conn, table)
    assert not (expected_columns & before), (
        f"partial fixture for {table} unexpectedly already has migrated columns"
    )

    db = _make_bare_db(conn)
    method = getattr(db, method_name)

    method()
    after_first = _column_names(conn, table)
    assert expected_columns <= after_first, f"{method_name} did not repair {table}"

    method()
    assert _column_names(conn, table) == after_first


@pytest.mark.parametrize("method_name,table,expected_columns", _NON_CONTENT_CONVERTED_METHODS)
def test_ensure_columns_does_not_commit_or_rollback(
    tmp_path: Path, method_name: str, table: str, expected_columns: set[str]
) -> None:
    """Transaction-boundary assertion: the additive migrations must NOT
    commit or rollback on the caller's connection (storage/migrations.py
    lines 148-151). We prove this by planting a sentinel row in an
    uncommitted transaction, running the migration, and verifying the
    sentinel is still uncommitted (visible on this connection but not yet
    durable), then rolling back and confirming BOTH the sentinel and the
    added columns are gone (review-t_cce76b68 F6).
    """
    conn = make_legacy_conn(tables=[table])
    # Plant a sentinel row in an UNCOMMITTED transaction.
    pk_col = "id" if table != "content_cache" else "bvid"
    sentinel_val = 999999 if pk_col == "id" else "SENTINEL_BVID"
    # Discover the other columns of the table so we can insert a valid row.
    cols_info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    insert_cols = []
    insert_vals = []
    for c in cols_info:
        name = str(c[1])
        notnull = bool(c[3])
        pk = bool(c[5])
        if pk:
            insert_cols.append(name)
            insert_vals.append(sentinel_val)
        elif notnull:
            # Provide a default for NOT NULL columns without a default.
            insert_cols.append(name)
            col_type = str(c[2]).upper()
            if "INT" in col_type:
                insert_vals.append(0)
            elif "REAL" in col_type or "FLOA" in col_type or "DOUB" in col_type:
                insert_vals.append(0.0)
            elif "TEXT" in col_type or "CHAR" in col_type or "CLOB" in col_type:
                insert_vals.append("")
            elif "TIMESTAMP" in col_type or "DATE" in col_type:
                insert_vals.append("2000-01-01 00:00:00")
            else:
                insert_vals.append("")
    cols_csv = ", ".join(insert_cols)
    placeholders = ", ".join("?" for _ in insert_vals)
    conn.execute(f"INSERT INTO {table} ({cols_csv}) VALUES ({placeholders})", insert_vals)

    db = _make_bare_db(conn)
    method = getattr(db, method_name)
    method()

    # The sentinel row must still be visible on this connection (i.e. the
    # migration did NOT commit), and the new columns must also be visible
    # (they were added in the same uncommitted transaction).
    row = conn.execute(
        f"SELECT {pk_col} FROM {table} WHERE {pk_col} = ?", (sentinel_val,)
    ).fetchone()
    assert row is not None, f"{method_name} unexpectedly committed the transaction"
    after = _column_names(conn, table)
    assert expected_columns <= after, f"{method_name} did not add expected columns"

    # Now rollback. Both the sentinel row and the added columns must vanish.
    conn.rollback()
    row_after_rollback = conn.execute(
        f"SELECT {pk_col} FROM {table} WHERE {pk_col} = ?", (sentinel_val,)
    ).fetchone()
    assert row_after_rollback is None, (
        f"{method_name} unexpectedly preserved the sentinel row across rollback"
    )
    cols_after_rollback = _column_names(conn, table)
    assert not (expected_columns & cols_after_rollback), (
        f"{method_name} columns survived rollback — transaction boundary violated"
    )


def test_ensure_columns_does_not_commit_or_rollback_content_cache(tmp_path: Path) -> None:
    """Dedicated transaction-boundary test for the content_cache table
    (TEXT primary key). Same contract as above: no commit, no rollback."""
    method_name = "_ensure_content_cache_runtime_columns"
    expected_columns = {
        "last_scored_at",
        "notification_sent",
        "notified_at",
        "pool_status",
        "recommended_at",
        "feedback_type",
        "feedback_at",
        "source",
    }
    conn = make_legacy_conn(tables=["content_cache"])
    conn.execute("INSERT INTO content_cache (bvid, title) VALUES ('SENTINEL_BVID', 'sentinel')")

    db = _make_bare_db(conn)
    method = getattr(db, method_name)
    method()

    row = conn.execute(
        "SELECT bvid FROM content_cache WHERE bvid = 'SENTINEL_BVID'"
    ).fetchone()
    assert row is not None, f"{method_name} unexpectedly committed the transaction"
    after = _column_names(conn, "content_cache")
    assert expected_columns <= after, f"{method_name} did not add expected columns"

    conn.rollback()
    row_after_rollback = conn.execute(
        "SELECT bvid FROM content_cache WHERE bvid = 'SENTINEL_BVID'"
    ).fetchone()
    assert row_after_rollback is None, (
        f"{method_name} unexpectedly preserved the sentinel row across rollback"
    )
    cols_after_rollback = _column_names(conn, "content_cache")
    assert not (expected_columns & cols_after_rollback), (
        f"{method_name} columns survived rollback — transaction boundary violated"
    )


def test_handwritten_multisource_backfill_still_runs_on_legacy() -> None:
    """The hand-written exceptions must still perform their backfill:
    _ensure_content_cache_multisource_columns backfills content_id = bvid
    after adding the multi-source columns."""
    conn = make_legacy_conn(tables=["content_cache"])
    conn.execute("INSERT INTO content_cache (bvid, title) VALUES ('BV1xx411c7mD', 't')")
    conn.commit()

    db = _make_bare_db(conn)
    db._ensure_content_cache_multisource_columns()
    conn.commit()

    row = conn.execute("SELECT bvid, content_id, source_platform FROM content_cache").fetchone()
    assert row["content_id"] == "BV1xx411c7mD", "multisource backfill did not run"
    assert row["source_platform"] == "bilibili"

    # Idempotent: second run adds nothing and does not overwrite.
    db._ensure_content_cache_multisource_columns()
    conn.commit()
    row2 = conn.execute("SELECT content_id FROM content_cache").fetchone()
    assert row2["content_id"] == "BV1xx411c7mD"
