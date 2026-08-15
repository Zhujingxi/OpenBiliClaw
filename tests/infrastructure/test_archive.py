"""Versioned local export/import archives preserve owned data safely."""

from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from openbiliclaw.core.config import load_settings
from openbiliclaw.infrastructure.archive import (
    ArchiveError,
    export_archive,
    import_archive,
)
from openbiliclaw.infrastructure.sqlite.schema import DEFAULT_MIGRATIONS, SchemaMigrator


async def _seed_database(data_dir: Path, *, migrations=DEFAULT_MIGRATIONS) -> Path:  # type: ignore[no-untyped-def]
    database_path = data_dir / "openbiliclaw.db"
    await SchemaMigrator(database_path, migrations=migrations).migrate()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO understanding_profiles(profile_id, revision, profile_json, updated_at)"
            " VALUES ('profile-1', 1, '{}', '2026-08-15T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO understanding_evidence"
            " (evidence_id, profile_id, observation_id, kind, weight, created_at)"
            " VALUES ('evidence-1', 'profile-1', NULL, 'explicit', 1.0,"
            " '2026-08-15T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO understanding_ledger(ledger_id, profile_id, entry_json, created_at)"
            " VALUES ('ledger-1', 'profile-1', '{\"claim\":\"cooking\"}',"
            " '2026-08-15T00:00:00+00:00')"
        )
        if len(migrations) >= 3:
            connection.execute(
                "INSERT INTO recommendation_candidates"
                " (candidate_id, state, candidate_json, created_at)"
                " VALUES ('candidate-1', 'discovered', '{\"title\":\"one\"}',"
                " '2026-08-15T00:00:00+00:00')"
            )
        if len(migrations) >= 7:
            connection.execute(
                "INSERT INTO policy_briefs(brief_id, episode_id, record_json, created_at)"
                " VALUES (?, 'episode-1', '{}', '2026-08-15T00:00:00+00:00')",
                ("brief_" + "a" * 32,),
            )
    return database_path


def _rows(path: Path, table: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


@pytest.mark.asyncio
async def test_export_import_round_trip_preserves_user_and_policy_planes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    database = await _seed_database(source)
    archive = tmp_path / "backup.obc"

    manifest = await export_archive(database, archive)
    destination = tmp_path / "fresh"
    imported = await import_archive(archive, destination)

    restored = destination / "openbiliclaw.db"
    assert imported.format_version == manifest.format_version == 1
    for table in (
        "understanding_profiles",
        "understanding_evidence",
        "understanding_ledger",
        "recommendation_candidates",
        "policy_briefs",
    ):
        assert _rows(restored, table) == _rows(database, table)
    assert manifest.table_counts["understanding_ledger"] == 1
    assert manifest.table_counts["recommendation_candidates"] == 1
    assert manifest.table_counts["policy_briefs"] == 1


@pytest.mark.asyncio
def _manifest() -> str:
    return json.dumps(
        {
            "format_version": 1,
            "app_version": "test",
            "created_at": "2026-08-15T00:00:00+00:00",
            "table_counts": {},
        }
    )


@pytest.mark.asyncio
async def test_import_rejects_unknown_format_version(tmp_path: Path) -> None:
    archive = tmp_path / "future.obc"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 999,
                    "app_version": "future",
                    "created_at": "2026-08-15T00:00:00+00:00",
                    "table_counts": {},
                }
            ),
        )
        bundle.writestr("openbiliclaw.db", b"not-used")

    with pytest.raises(ArchiveError, match="format version"):
        await import_archive(archive, tmp_path / "restore")


@pytest.mark.asyncio
async def test_import_refuses_nonempty_destination_without_force(tmp_path: Path) -> None:
    source = tmp_path / "source"
    database = await _seed_database(source)
    archive = tmp_path / "backup.obc"
    await export_archive(database, archive)
    destination = tmp_path / "occupied"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ArchiveError, match="not empty"):
        await import_archive(archive, destination)
    assert marker.read_text(encoding="utf-8") == "keep"

    await import_archive(archive, destination, force=True)
    assert not marker.exists()
    assert (destination / "openbiliclaw.db").exists()


@pytest.mark.asyncio
async def test_exported_config_keeps_refs_but_redacts_verifiers(tmp_path: Path) -> None:
    database = await _seed_database(tmp_path / "source")
    config = tmp_path / "config.toml"
    config.write_text(
        '[model]\nsecret_ref = "vault:cred_model"\nprovider = "x"\nmodel_name = "y"\n'
        '[host]\npassword_hash = "pbkdf2:100000:abcdef:deadbeef"\n',
        encoding="utf-8",
    )
    archive = tmp_path / "config.obc"

    await export_archive(database, archive, config_path=config)

    with zipfile.ZipFile(archive) as bundle:
        exported = bundle.read("config.toml").decode()
    assert "vault:cred_model" in exported
    assert "pbkdf2:100000:abcdef:deadbeef" not in exported
    assert "password_hash redacted from export" in exported

    destination = tmp_path / "restored"
    await import_archive(archive, destination)
    restored_config = destination / "config.toml"
    assert restored_config.read_text(encoding="utf-8") == exported
    assert load_settings(restored_config, environ={}).host.password_hash is None


@pytest.mark.asyncio
async def test_import_migrates_older_database_snapshot_forward(tmp_path: Path) -> None:
    source = tmp_path / "old"
    database = await _seed_database(source, migrations=DEFAULT_MIGRATIONS[:6])
    archive = tmp_path / "old.obc"
    await export_archive(database, archive)

    destination = tmp_path / "restored"
    await import_archive(archive, destination)

    restored = destination / "openbiliclaw.db"
    with sqlite3.connect(restored) as connection:
        version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert version == len(DEFAULT_MIGRATIONS)
    assert "policy_briefs" in tables
    assert "auth_tokens" in tables


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("members", "message"),
    (
        ({"manifest.json": _manifest()}, "missing required members"),
        (
            {
                "manifest.json": _manifest(),
                "openbiliclaw.db": b"unused",
                "../evil": b"escape",
            },
            "unexpected members",
        ),
    ),
)
async def test_import_rejects_missing_and_unexpected_members(
    tmp_path: Path, members: dict[str, str | bytes], message: str
) -> None:
    archive = tmp_path / "invalid.obc"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, content in members.items():
            bundle.writestr(name, content)

    with pytest.raises(ArchiveError, match=message):
        await import_archive(archive, tmp_path / "restore")
    assert not (tmp_path / "evil").exists()


@pytest.mark.asyncio
async def test_import_rejects_corrupt_zip_and_non_sqlite_member(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.obc"
    corrupt.write_bytes(b"not a zip")
    with pytest.raises(ArchiveError, match="invalid archive"):
        await import_archive(corrupt, tmp_path / "corrupt-restore")

    invalid_database = tmp_path / "invalid-database.obc"
    with zipfile.ZipFile(invalid_database, "w") as bundle:
        bundle.writestr("manifest.json", _manifest())
        bundle.writestr("openbiliclaw.db", b"not sqlite")
    with pytest.raises(ArchiveError, match="invalid or incompatible"):
        await import_archive(invalid_database, tmp_path / "database-restore")


@pytest.mark.asyncio
async def test_export_rejects_missing_database(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError, match="database does not exist"):
        await export_archive(tmp_path / "missing.db", tmp_path / "backup.obc")


@pytest.mark.asyncio
async def test_export_redacts_single_quoted_hash_and_fails_closed_on_unhandled_hash(
    tmp_path: Path,
) -> None:
    database = await _seed_database(tmp_path / "source")
    single = tmp_path / "single.toml"
    single.write_text(
        "[host]\npassword_hash = 'pbkdf2:100000:abcdef:deadbeef'\n",
        encoding="utf-8",
    )
    archive = tmp_path / "single.obc"
    await export_archive(database, archive, config_path=single)
    with zipfile.ZipFile(archive) as bundle:
        assert "deadbeef" not in bundle.read("config.toml").decode()

    dotted = tmp_path / "dotted.toml"
    dotted.write_text(
        "host.password_hash = 'pbkdf2:100000:abcdef:deadbeef'\n",
        encoding="utf-8",
    )
    dotted_archive = tmp_path / "dotted.obc"
    await export_archive(database, dotted_archive, config_path=dotted)
    with zipfile.ZipFile(dotted_archive) as bundle:
        assert "deadbeef" not in bundle.read("config.toml").decode()

    triple = tmp_path / "triple.toml"
    triple.write_text(
        '[host]\npassword_hash = """pbkdf2:100000:abcdef:deadbeef"""\n',
        encoding="utf-8",
    )
    with pytest.raises(ArchiveError, match="could not be safely redacted"):
        await export_archive(database, tmp_path / "triple.obc", config_path=triple)


@pytest.mark.asyncio
async def test_export_wraps_invalid_config_as_archive_error(tmp_path: Path) -> None:
    database = await _seed_database(tmp_path / "source")
    malformed = tmp_path / "malformed.toml"
    malformed.write_text("[host\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="invalid config"):
        await export_archive(database, tmp_path / "backup.obc", config_path=malformed)


@pytest.mark.asyncio
async def test_import_rejects_newer_database_schema(tmp_path: Path) -> None:
    database = await _seed_database(tmp_path / "source")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (len(DEFAULT_MIGRATIONS) + 1, "2026-08-15T00:00:00+00:00"),
        )
    archive = tmp_path / "newer.obc"
    await export_archive(database, archive)

    with pytest.raises(ArchiveError, match="newer version; upgrade the app"):
        await import_archive(archive, tmp_path / "restore")


@pytest.mark.asyncio
async def test_force_import_restores_prior_directory_on_install_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = await _seed_database(tmp_path / "source")
    config = tmp_path / "config.toml"
    config.write_text("[host]\napi_port = 8420\n", encoding="utf-8")
    archive = tmp_path / "backup.obc"
    await export_archive(database, archive, config_path=config)
    destination = tmp_path / "occupied"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    real_replace = os.replace

    def fail_config_install(source: Path, target: Path) -> None:
        if Path(target).name == "config.toml":
            raise OSError("simulated install failure")
        real_replace(source, target)

    monkeypatch.setattr("openbiliclaw.infrastructure.archive.os.replace", fail_config_install)
    with pytest.raises(OSError, match="simulated install failure"):
        await import_archive(archive, destination, force=True)

    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (destination / "openbiliclaw.db").exists()
    assert not tuple(tmp_path.glob(".occupied.pre-import-*"))
