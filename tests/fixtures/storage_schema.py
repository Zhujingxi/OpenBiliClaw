"""Programmatic SQLite schema fixtures for migration / repository tests.

These functions build empty, legacy, current, and partial (half-migrated)
databases **in code**. Never check a binary ``*.db`` file into the repo —
programmatic fixtures keep the schema state reviewable and reproducible.

Used by ``tests/test_storage_schema_migrations.py`` (Phase 0) and the
migration-dedup tests for the ``ensure_columns`` helper (Phase 2A).

All ``Database``-backed fixtures take a ``tmp_path`` because ``Database``
requires a real file path (WAL mode, busy_timeout, and the legacy
``saved_sync_migrations`` bookkeeping all assume file semantics). Pure
sqlite3 fixtures (used only for inspecting the raw schema without going
through ``Database``) can stay in-memory.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(r[0]) for r in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def make_empty_db() -> sqlite3.Connection:
    """An in-memory raw connection with no tables at all (no Database)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def make_current_db(tmp_path: Path) -> sqlite3.Connection:
    """A fully migrated database, built through the production code path."""
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "current.db")
    db.initialize()
    return db.conn


def make_legacy_conn(tables: Iterable[str] | None = None) -> sqlite3.Connection:
    """A raw in-memory connection with a minimal "legacy" schema: only the
    listed tables, each with a deliberately small column set that predates
    the additive migrations.

    Useful for exercising the column-level ``_ensure_*_columns`` methods
    against a pre-migration shape without going through ``Database``.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    wanted = set(tables or _DEFAULT_LEGACY_TABLES)
    if "events" in wanted:
        conn.execute(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}'
            )
            """
        )
    if "recommendations" in wanted:
        conn.execute(
            """
            CREATE TABLE recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                score REAL DEFAULT 0.0
            )
            """
        )
    if "content_cache" in wanted:
        conn.execute(
            """
            CREATE TABLE content_cache (
                bvid TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    if "llm_usage" in wanted:
        conn.execute(
            """
            CREATE TABLE llm_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tokens INTEGER DEFAULT 0
            )
            """
        )
    if "discovery_candidates" in wanted:
        conn.execute(
            """
            CREATE TABLE discovery_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid TEXT NOT NULL,
                status TEXT DEFAULT 'pending'
            )
            """
        )
    conn.commit()
    return conn


_DEFAULT_LEGACY_TABLES = (
    "events",
    "recommendations",
    "content_cache",
    "llm_usage",
    "discovery_candidates",
)


def make_partial_db(
    tmp_path: Path, target_table: str, missing_columns: Iterable[str]
) -> sqlite3.Connection:
    """A "half-migrated" database: starts from the current schema and drops
    the listed columns from ``target_table`` (by rebuilding the table
    without them). Simulates a crash mid-migration.

    Only suitable for additive ``ALTER TABLE ... ADD COLUMN`` migrations
    (the migration-dedup target). Not suitable for transforms.
    """
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "partial.db")
    db.initialize()
    conn = db.conn
    missing = set(missing_columns)
    if not missing:
        return conn

    # Rebuild the table without the target columns. We must also drop any
    # indexes that reference the dropped columns, otherwise SQLite will
    # refuse to rebuild.
    indexes = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (target_table,),
    ).fetchall()
    for idx in indexes:
        idx_sql = str(idx["sql"] or "")
        if any(col in idx_sql for col in missing):
            conn.execute(f"DROP INDEX IF EXISTS {idx['name']}")

    cols_info = conn.execute(f"PRAGMA table_info({target_table})").fetchall()
    keep = [c for c in cols_info if str(c["name"]) not in missing]
    if not keep:
        raise ValueError(f"cannot drop every column of {target_table}")
    keep_names = [str(c["name"]) for c in keep]
    col_defs = []
    for c in keep:
        pk = " PRIMARY KEY" if c["pk"] else ""
        notnull = " NOT NULL" if c["notnull"] else ""
        default = f" DEFAULT {c['dflt_value']}" if c["dflt_value"] is not None else ""
        col_defs.append(f"{c['name']} {c['type']}{pk}{notnull}{default}")
    conn.execute(f"ALTER TABLE {target_table} RENAME TO {target_table}__old")
    conn.execute(f"CREATE TABLE {target_table} ({', '.join(col_defs)})")
    cols_csv = ", ".join(keep_names)
    conn.execute(
        f"INSERT INTO {target_table} ({cols_csv}) SELECT {cols_csv} FROM {target_table}__old"
    )
    conn.execute(f"DROP TABLE {target_table}__old")
    conn.commit()
    return conn


__all__ = [
    "make_current_db",
    "make_empty_db",
    "make_legacy_conn",
    "make_partial_db",
    "_column_names",
    "_table_names",
]
