from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.infrastructure.sqlite.schema import (
    TARGET_TABLE_OWNERS,
    Migration,
    SchemaMigrator,
    backup_before_destructive_migration,
)


async def test_target_schema_has_owned_aggregates_and_constraints(tmp_path: Path) -> None:
    path = tmp_path / "target.db"
    migrator = SchemaMigrator(path)
    assert await migrator.migrate() == 2
    assert await migrator.migrate() == 2
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert set(TARGET_TABLE_OWNERS) <= tables
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        conn.execute(
            "INSERT INTO content_references(provider, external_id, kind) VALUES(?,?,?)",
            ("bilibili", "BV1", "video"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO content_references(provider, external_id, kind) VALUES(?,?,?)",
                ("bilibili", "BV1", "video"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO observations"
                "(observation_id, kind, occurred_at, strength) VALUES(?,?,?,?)",
                ("o1", "view", "now", 2.0),
            )
        conn.execute(
            "INSERT INTO observations"
            "(observation_id, kind, occurred_at, producer, idempotency_key)"
            " VALUES(?,?,?,?,?)",
            ("o2", "view", "now", "extension", "request-1"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO observations"
                "(observation_id, kind, occurred_at, producer, idempotency_key)"
                " VALUES(?,?,?,?,?)",
                ("o3", "view", "now", "extension", "request-1"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO content_cache(content_id,title,fetched_at) VALUES(?,?,?)",
                (999, "missing parent", "now"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO access_metadata"
                "(access_id,provider,method,credential_ref,state,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                ("a1", "x", "manual", "plaintext-secret", "verified", "now", "now"),
            )


async def test_destructive_migration_requires_decision_and_verified_backup(tmp_path: Path) -> None:
    path = tmp_path / "user.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE user_data(value TEXT NOT NULL)")
        conn.execute("INSERT INTO user_data VALUES(?)", ("keep-me",))
    config = tmp_path / "config.toml"
    config.write_text("[safe]\nvalue = 1\n", encoding="utf-8")
    migration = Migration(1, ("DROP TABLE user_data",), destructive=True)
    migrator = SchemaMigrator(path, migrations=(migration,))
    with pytest.raises(PermissionError):
        await migrator.migrate()
    with pytest.raises(ValueError):
        await migrator.migrate(allow_destructive=True)
    assert (
        await migrator.migrate(
            allow_destructive=True, backup_dir=tmp_path / "backups", config_paths=(config,)
        )
        == 1
    )
    backups = list((tmp_path / "backups").iterdir())
    assert len(backups) == 1
    backed_up_db = backups[0] / "user.db"
    with sqlite3.connect(backed_up_db) as conn:
        assert conn.execute("SELECT value FROM user_data").fetchone() == ("keep-me",)


async def test_unversioned_legacy_database_stops_for_reset_or_import_decision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE legacy_user_data(value TEXT)")
        conn.execute("INSERT INTO legacy_user_data VALUES(?)", ("keep",))
    with pytest.raises(RuntimeError, match="reset or import decision"):
        await SchemaMigrator(path).migrate()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM legacy_user_data").fetchone() == ("keep",)
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() == (0,)


async def test_migration_failure_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "atomic.db"
    migration = Migration(1, ("CREATE TABLE temporary(value TEXT)", "INVALID SQL"))
    with pytest.raises(sqlite3.OperationalError):
        await SchemaMigrator(path, migrations=(migration,)).migrate()
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name=?", ("temporary",)
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() == (0,)


async def test_backup_refuses_missing_database_without_creating_it(tmp_path: Path) -> None:
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        await backup_before_destructive_migration(path, tmp_path / "backups")
    assert not path.exists()
    assert not (tmp_path / "backups").exists()


async def test_backup_refuses_corrupt_database(tmp_path: Path) -> None:
    path = tmp_path / "broken.db"
    path.write_bytes(b"not sqlite")
    with pytest.raises(RuntimeError, match="backup"):
        await backup_before_destructive_migration(path, tmp_path / "backups")
