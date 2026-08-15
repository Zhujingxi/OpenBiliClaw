"""Versioned, local-only export/import of the owned SQLite state."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import tempfile
import tomllib
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Final

from pydantic import AwareDatetime, ConfigDict, Field, ValidationError

from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.core.config import load_settings
from openbiliclaw.infrastructure.sqlite.schema import DEFAULT_MIGRATIONS, SchemaMigrator

FORMAT_VERSION: Final = 1
_DATABASE_MEMBER: Final = "openbiliclaw.db"
_MANIFEST_MEMBER: Final = "manifest.json"
_CONFIG_MEMBER: Final = "config.toml"
_PASSWORD_HASH = re.compile(
    r"""(?m)^\s*(?:host\.)?password_hash\s*=\s*(?:"(?:[^"\\]|\\.)*"|'[^']*')\s*(?:#.*)?$"""
)
_PASSWORD_ASSIGNMENT = re.compile(r"(?m)^\s*(?:host\.)?password_hash\s*=")


class ArchiveError(ValueError):
    """Safe export/import validation failure."""


class ArchiveManifest(StrictBaseModel):
    """Compatibility and integrity metadata stored inside every archive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: int = Field(ge=1)
    app_version: str = Field(min_length=1, max_length=100)
    created_at: AwareDatetime
    table_counts: dict[str, int]


def _snapshot(source_path: Path, target_path: Path) -> None:
    if not source_path.is_file():
        raise ArchiveError(f"database does not exist: {source_path}")
    with (
        closing(sqlite3.connect(source_path)) as source,
        closing(sqlite3.connect(target_path)) as target,
    ):
        source.backup(target)


def _table_counts(database_path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(database_path)) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        return {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        }


def _redacted_config(path: Path) -> str:
    if not path.is_file():
        raise ArchiveError(f"config does not exist: {path}")
    # Validation guarantees secret-bearing fields are opaque references, never inline values.
    try:
        load_settings(path, environ={})
    except (tomllib.TOMLDecodeError, ValidationError) as error:
        raise ArchiveError(f"invalid config: {path}") from error
    document = path.read_text(encoding="utf-8")
    redacted = _PASSWORD_HASH.sub("# password_hash redacted from export", document)
    if _PASSWORD_ASSIGNMENT.search(redacted):
        raise ArchiveError("config password_hash could not be safely redacted")
    return redacted


def _write_archive(
    archive_path: Path,
    snapshot_path: Path,
    manifest: ArchiveManifest,
    config: str | None,
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_suffix(archive_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as bundle:
            bundle.writestr(
                _MANIFEST_MEMBER,
                json.dumps(manifest.model_dump(mode="json"), sort_keys=True),
            )
            bundle.write(snapshot_path, _DATABASE_MEMBER)
            if config is not None:
                bundle.writestr(_CONFIG_MEMBER, config)
        os.replace(temporary, archive_path)
    finally:
        temporary.unlink(missing_ok=True)


async def export_archive(
    database_path: Path,
    archive_path: Path,
    *,
    config_path: Path | None = None,
) -> ArchiveManifest:
    """Create an atomic archive from a consistent SQLite backup snapshot."""

    config = await asyncio.to_thread(_redacted_config, config_path) if config_path else None
    with tempfile.TemporaryDirectory(prefix="openbiliclaw-export-") as temporary:
        snapshot = Path(temporary) / _DATABASE_MEMBER
        await asyncio.to_thread(_snapshot, database_path, snapshot)
        manifest = ArchiveManifest(
            format_version=FORMAT_VERSION,
            app_version=version("openbiliclaw"),
            created_at=datetime.now(UTC),
            table_counts=await asyncio.to_thread(_table_counts, snapshot),
        )
        await asyncio.to_thread(_write_archive, archive_path, snapshot, manifest, config)
    return manifest


def _read_bundle(archive_path: Path, staging: Path) -> tuple[ArchiveManifest, Path | None]:
    try:
        with zipfile.ZipFile(archive_path) as bundle:
            names = set(bundle.namelist())
            if not {_MANIFEST_MEMBER, _DATABASE_MEMBER} <= names:
                raise ArchiveError("archive is missing required members")
            allowed = {_MANIFEST_MEMBER, _DATABASE_MEMBER, _CONFIG_MEMBER}
            if not names <= allowed:
                raise ArchiveError("archive contains unexpected members")
            manifest = ArchiveManifest.model_validate_json(bundle.read(_MANIFEST_MEMBER))
            if manifest.format_version != FORMAT_VERSION:
                raise ArchiveError(f"unsupported archive format version {manifest.format_version}")
            database = staging / _DATABASE_MEMBER
            with bundle.open(_DATABASE_MEMBER) as source, database.open("wb") as target:
                shutil.copyfileobj(source, target)
            config = staging / _CONFIG_MEMBER if _CONFIG_MEMBER in names else None
            if config is not None:
                config.write_bytes(bundle.read(_CONFIG_MEMBER))
            return manifest, config
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, ValidationError) as error:
        raise ArchiveError("invalid archive") from error


def _destination_nonempty(data_dir: Path) -> bool:
    return data_dir.exists() and any(data_dir.iterdir())


def _replace_destination(data_dir: Path, staged_database: Path, staged_config: Path | None) -> None:
    previous: Path | None = None
    if data_dir.exists():
        previous = data_dir.with_name(f".{data_dir.name}.pre-import-{uuid.uuid4().hex}")
        os.replace(data_dir, previous)
    try:
        data_dir.mkdir(parents=True)
        os.replace(staged_database, data_dir / _DATABASE_MEMBER)
        if staged_config is not None:
            os.replace(staged_config, data_dir / _CONFIG_MEMBER)
    except BaseException:
        if data_dir.exists():
            shutil.rmtree(data_dir)
        if previous is not None:
            os.replace(previous, data_dir)
        raise
    if previous is not None:
        shutil.rmtree(previous)


async def import_archive(
    archive_path: Path,
    data_dir: Path,
    *,
    force: bool = False,
) -> ArchiveManifest:
    """Validate, migrate, then atomically install an archive into a data directory."""

    if _destination_nonempty(data_dir) and not force:
        raise ArchiveError(f"destination data directory is not empty: {data_dir}")
    # Stage beside the destination so final os.replace calls stay on one filesystem.
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".openbiliclaw-import-", dir=data_dir.parent
    ) as temporary:
        staging = Path(temporary)
        manifest, config = await asyncio.to_thread(_read_bundle, archive_path, staging)
        database = staging / _DATABASE_MEMBER
        try:
            schema_version = await SchemaMigrator(database).migrate()
        except (OSError, sqlite3.DatabaseError, RuntimeError) as error:
            raise ArchiveError("archive database is invalid or incompatible") from error
        if schema_version > len(DEFAULT_MIGRATIONS):
            raise ArchiveError("archive was created by a newer version; upgrade the app")
        await asyncio.to_thread(_replace_destination, data_dir, database, config)
    return manifest
