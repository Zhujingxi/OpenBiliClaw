"""Target SQLite schema, migrations, and destructive-change backups."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

TARGET_TABLE_OWNERS: Final[dict[str, str]] = {
    "access_metadata": "Provider Access",
    "content_references": "Content Integration",
    "content_cache": "Content Providers",
    "observations": "Observation Ingress",
    "understanding_profiles": "User Understanding",
    "understanding_evidence": "User Understanding",
    "recommendation_inventory": "Discovery & Recommendation",
    "recommendation_history": "Discovery & Recommendation",
    "assistant_conversations": "Assistant",
    "assistant_messages": "Assistant",
    "pending_actions": "Application Workflows",
    "ai_usage_attribution": "AI Runtime",
}

_SCHEMA_V1: Final[tuple[str, ...]] = (
    """CREATE TABLE access_metadata (
        access_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        method TEXT NOT NULL,
        credential_ref TEXT NOT NULL DEFAULT '' CHECK(
            credential_ref = '' OR (
                length(credential_ref) = 37
                AND substr(credential_ref, 1, 5) = 'cred_'
                AND substr(credential_ref, 6) NOT GLOB '*[^0-9a-f]*'
            )
        ),
        state TEXT NOT NULL CHECK(state IN ('pending','verified','disabled','failed')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE content_references (
        content_id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL,
        external_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        canonical_url TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(provider, external_id)
    )""",
    """CREATE TABLE content_cache (
        content_id INTEGER PRIMARY KEY REFERENCES content_references(content_id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        projection_json TEXT NOT NULL DEFAULT '{}',
        fetched_at TEXT NOT NULL,
        expires_at TEXT
    )""",
    """CREATE TABLE observations (
        observation_id TEXT PRIMARY KEY,
        content_id INTEGER REFERENCES content_references(content_id) ON DELETE SET NULL,
        kind TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        strength REAL NOT NULL DEFAULT 1 CHECK(strength >= 0 AND strength <= 1),
        producer TEXT NOT NULL DEFAULT '',
        idempotency_key TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE UNIQUE INDEX observations_idempotency
        ON observations(producer, idempotency_key) WHERE idempotency_key <> ''""",
    """CREATE TABLE understanding_profiles (
        profile_id TEXT PRIMARY KEY,
        revision INTEGER NOT NULL CHECK(revision >= 0),
        profile_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE understanding_evidence (
        evidence_id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL REFERENCES understanding_profiles(profile_id) ON DELETE CASCADE,
        observation_id TEXT REFERENCES observations(observation_id) ON DELETE SET NULL,
        kind TEXT NOT NULL,
        weight REAL NOT NULL CHECK(weight >= 0 AND weight <= 1),
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE recommendation_inventory (
        candidate_id TEXT PRIMARY KEY,
        content_id INTEGER NOT NULL UNIQUE
            REFERENCES content_references(content_id) ON DELETE CASCADE,
        state TEXT NOT NULL CHECK(state IN ('pending','evaluated','selected','rejected','expired')),
        score REAL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE recommendation_history (
        recommendation_id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL
            REFERENCES recommendation_inventory(candidate_id) ON DELETE RESTRICT,
        shown_at TEXT NOT NULL,
        outcome TEXT NOT NULL DEFAULT '',
        UNIQUE(candidate_id, shown_at)
    )""",
    """CREATE TABLE assistant_conversations (
        conversation_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE assistant_messages (
        message_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL
            REFERENCES assistant_conversations(conversation_id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK(role IN ('user','assistant','tool')),
        content_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        idempotency_key TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE UNIQUE INDEX assistant_messages_idempotency
        ON assistant_messages(conversation_id, idempotency_key) WHERE idempotency_key <> ''""",
    """CREATE TABLE pending_actions (
        action_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','running','completed','failed','cancelled')),
        payload_json TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE CHECK(idempotency_key <> ''),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE ai_usage_attribution (
        usage_id TEXT PRIMARY KEY,
        operation TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        request_count INTEGER NOT NULL CHECK(request_count >= 0),
        input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
        output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
        cost_micros INTEGER NOT NULL CHECK(cost_micros >= 0),
        occurred_at TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE CHECK(idempotency_key <> '')
    )""",
)


@dataclass(frozen=True, slots=True)
class Migration:
    """One ordered atomic schema migration."""

    version: int
    statements: tuple[str, ...]
    destructive: bool = False


DEFAULT_MIGRATIONS: Final[tuple[Migration, ...]] = (Migration(1, _SCHEMA_V1),)


class SchemaMigrator:
    """Apply ordered migrations, backing up before destructive changes."""

    def __init__(
        self,
        database_path: Path,
        *,
        migrations: tuple[Migration, ...] = DEFAULT_MIGRATIONS,
    ) -> None:
        self._path = database_path
        self._migrations = migrations

    async def migrate(
        self,
        *,
        allow_destructive: bool = False,
        backup_dir: Path | None = None,
        config_paths: tuple[Path, ...] = (),
    ) -> int:
        """Apply pending migrations and return the resulting version."""

        pending = await asyncio.to_thread(self._pending_migrations)
        destructive = any(migration.destructive for migration in pending)
        if (
            pending
            and not destructive
            and await asyncio.to_thread(self._has_unversioned_application_tables)
        ):
            raise RuntimeError("unversioned database requires an explicit reset or import decision")
        if destructive and not allow_destructive:
            raise PermissionError("destructive migration requires an explicit decision")
        if destructive:
            if backup_dir is None:
                raise ValueError("destructive migration requires a backup directory")
            await backup_before_destructive_migration(
                self._path, backup_dir, config_paths=config_paths
            )
        return await asyncio.to_thread(self._apply, pending)

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _has_unversioned_application_tables(self) -> bool:
        with closing(self._connect()) as connection:
            current = self._current_version(connection)
            tables = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> ?",
                ("schema_migrations",),
            ).fetchall()
        return current == 0 and bool(tables)

    def _pending_migrations(self) -> tuple[Migration, ...]:
        with closing(self._connect()) as connection:
            current = self._current_version(connection)
        return tuple(migration for migration in self._migrations if migration.version > current)

    def _apply(self, pending: tuple[Migration, ...]) -> int:
        with closing(self._connect()) as connection:
            for migration in pending:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS schema_migrations ("
                        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                    )
                    for statement in migration.statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                        (migration.version, datetime.now(UTC).isoformat()),
                    )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
            return self._current_version(connection)

    @staticmethod
    def _current_version(connection: sqlite3.Connection) -> int:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            ("schema_migrations",),
        ).fetchone()
        if exists is None:
            return 0
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        return int(row[0]) if row is not None else 0


async def backup_before_destructive_migration(
    database_path: Path,
    backup_root: Path,
    *,
    config_paths: tuple[Path, ...] = (),
) -> Path:
    """Create and verify a timestamped SQLite/config backup."""

    return await asyncio.to_thread(
        _backup_before_destructive_migration, database_path, backup_root, config_paths
    )


def _backup_before_destructive_migration(
    database_path: Path, backup_root: Path, config_paths: tuple[Path, ...]
) -> Path:
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_root / stamp
    destination.mkdir(parents=True, exist_ok=False)
    database_copy = destination / database_path.name
    try:
        with (
            closing(sqlite3.connect(database_path)) as source,
            closing(sqlite3.connect(database_copy)) as target,
        ):
            source.backup(target)
        with closing(sqlite3.connect(database_copy)) as verified:
            row = verified.execute("PRAGMA integrity_check").fetchone()
            if row != ("ok",):
                raise RuntimeError("backup database integrity check failed")
        for config_path in config_paths:
            if not config_path.is_file():
                continue
            copied = destination / config_path.name
            shutil.copy2(config_path, copied)
            if _digest(config_path) != _digest(copied):
                raise RuntimeError("backup config checksum verification failed")
    except (RuntimeError, sqlite3.Error, OSError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise RuntimeError("backup creation or verification failed") from exc
    return destination


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
