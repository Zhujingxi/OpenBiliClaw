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
    "understanding_proposals": "User Understanding",
    "understanding_ledger": "User Understanding",
    "understanding_checkpoints": "User Understanding",
    "recommendation_inventory": "Discovery & Recommendation",
    "recommendation_history": "Discovery & Recommendation",
    "recommendation_candidates": "Discovery & Recommendation",
    "recommendation_evaluations": "Discovery & Recommendation",
    "recommendation_rejections": "Discovery & Recommendation",
    "recommendation_admissions": "Discovery & Recommendation",
    "recommendation_selections": "Discovery & Recommendation",
    "recommendation_shown": "Discovery & Recommendation",
    "recommendation_feedback": "Discovery & Recommendation",
    "recommendation_expressions": "Discovery & Recommendation",
    "assistant_conversations": "Assistant",
    "assistant_messages": "Assistant",
    "pending_actions": "Application Workflows",
    "workflow_idempotency": "Application Workflows",
    "ai_usage_attribution": "AI Runtime",
    "auth_tokens": "API & CLI Hosts",
    "policy_briefs": "Discovery & Recommendation",
    "policy_hypotheses": "Discovery & Recommendation",
    "policy_lessons": "Discovery & Recommendation",
    "policy_outcomes": "Discovery & Recommendation",
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
    """CREATE TABLE understanding_proposals (
        proposal_id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        analyzer_id TEXT NOT NULL,
        proposal_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE understanding_ledger (
        ledger_id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL REFERENCES understanding_profiles(profile_id) ON DELETE CASCADE,
        entry_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE understanding_checkpoints (
        analyzer_id TEXT PRIMARY KEY,
        cursor TEXT NOT NULL,
        updated_at TEXT NOT NULL
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


_SCHEMA_V2: Final[tuple[str, ...]] = (
    """CREATE TABLE IF NOT EXISTS understanding_proposals (
        proposal_id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        analyzer_id TEXT NOT NULL,
        proposal_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS understanding_ledger (
        ledger_id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL REFERENCES understanding_profiles(profile_id) ON DELETE CASCADE,
        entry_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS understanding_checkpoints (
        analyzer_id TEXT PRIMARY KEY,
        cursor TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
)

_SCHEMA_V3: Final[tuple[str, ...]] = (
    """CREATE TABLE IF NOT EXISTS recommendation_candidates (
        candidate_id TEXT PRIMARY KEY,
        state TEXT NOT NULL CHECK(state IN (
            'discovered','normalized','prefiltered','evaluated','admitted',
            'rejected','selected','shown','interacted','expired'
        )),
        candidate_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS recommendation_evaluations (
        evaluation_id TEXT PRIMARY KEY,
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS recommendation_selections (
        recommendation_id TEXT PRIMARY KEY,
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS recommendation_shown (
        shown_id TEXT PRIMARY KEY,
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS recommendation_feedback (
        feedback_id TEXT PRIMARY KEY,
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS recommendation_expressions (
        recommendation_id TEXT PRIMARY KEY,
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
)

_SCHEMA_V4: Final[tuple[str, ...]] = (
    """CREATE TABLE IF NOT EXISTS recommendation_rejections (
        rejection_id TEXT PRIMARY KEY CHECK(rejection_id GLOB 'reject_[0-9a-f]*'),
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS recommendation_admissions (
        admission_id TEXT PRIMARY KEY CHECK(admission_id GLOB 'admit_[0-9a-f]*'),
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TRIGGER IF NOT EXISTS recommendation_candidate_valid_transition
        BEFORE UPDATE OF state ON recommendation_candidates
        WHEN NOT (
            (OLD.state='discovered' AND NEW.state IN ('normalized','rejected')) OR
            (OLD.state='normalized' AND NEW.state IN ('prefiltered','rejected')) OR
            (OLD.state='prefiltered' AND NEW.state IN ('evaluated','rejected')) OR
            (OLD.state='evaluated' AND NEW.state IN ('admitted','rejected')) OR
            (OLD.state='admitted' AND NEW.state IN ('selected','expired')) OR
            (OLD.state='selected' AND NEW.state IN ('shown','expired')) OR
            (OLD.state='shown' AND NEW.state IN ('interacted','expired'))
        )
        BEGIN SELECT RAISE(ABORT, 'invalid recommendation candidate transition'); END""",
)

_SCHEMA_V5: Final[tuple[str, ...]] = (
    "ALTER TABLE assistant_conversations ADD COLUMN conversation_json TEXT NOT NULL DEFAULT '{}'",
)

_SCHEMA_V6: Final[tuple[str, ...]] = (
    """CREATE TABLE workflow_idempotency (
        idempotency_key TEXT PRIMARY KEY,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
)

_SCHEMA_V7: Final[tuple[str, ...]] = (
    """CREATE TABLE IF NOT EXISTS policy_briefs (
        brief_id TEXT PRIMARY KEY CHECK(brief_id GLOB 'brief_[0-9a-f]*'),
        episode_id TEXT NOT NULL,
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS policy_hypotheses (
        hypothesis_id TEXT PRIMARY KEY CHECK(hypothesis_id GLOB 'hyp_[0-9a-f]*'),
        arm TEXT NOT NULL,
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS policy_lessons (
        lesson_id TEXT PRIMARY KEY CHECK(lesson_id GLOB 'lesson_[0-9a-f]*'),
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS policy_outcomes (
        outcome_id TEXT PRIMARY KEY CHECK(outcome_id GLOB 'outcome_[0-9a-f]*'),
        hypothesis_id TEXT NOT NULL,
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS policy_outcomes_hypothesis ON policy_outcomes(hypothesis_id)",
    """CREATE TRIGGER IF NOT EXISTS policy_briefs_append_only_update
        BEFORE UPDATE ON policy_briefs
        BEGIN SELECT RAISE(ABORT, 'policy journal is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS policy_briefs_append_only_delete
        BEFORE DELETE ON policy_briefs
        BEGIN SELECT RAISE(ABORT, 'policy journal is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS policy_hypotheses_append_only_update
        BEFORE UPDATE ON policy_hypotheses
        BEGIN SELECT RAISE(ABORT, 'policy journal is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS policy_hypotheses_append_only_delete
        BEFORE DELETE ON policy_hypotheses
        BEGIN SELECT RAISE(ABORT, 'policy journal is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS policy_lessons_append_only_update
        BEFORE UPDATE ON policy_lessons
        BEGIN SELECT RAISE(ABORT, 'policy journal is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS policy_lessons_append_only_delete
        BEFORE DELETE ON policy_lessons
        BEGIN SELECT RAISE(ABORT, 'policy journal is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS policy_outcomes_append_only_update
        BEFORE UPDATE ON policy_outcomes
        BEGIN SELECT RAISE(ABORT, 'policy journal is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS policy_outcomes_append_only_delete
        BEFORE DELETE ON policy_outcomes
        BEGIN SELECT RAISE(ABORT, 'policy journal is append-only'); END""",
)

_SCHEMA_V8: Final[tuple[str, ...]] = (
    """UPDATE recommendation_candidates
       SET candidate_json = json_set(
           candidate_json,
           '$.provenance.provider',
           json_extract(candidate_json, '$.preview.ref.provider_id.value'),
           '$.provenance.channel',
           NULL
       )
       WHERE json_type(candidate_json, '$.provenance.provider') IS NULL""",
)

_SCHEMA_V9: Final[tuple[str, ...]] = (
    """CREATE TABLE auth_tokens (
        token_id TEXT PRIMARY KEY CHECK(token_id GLOB 'at_[0-9a-f]*'),
        label TEXT NOT NULL CHECK(label IN ('session', 'extension')),
        token_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )""",
)


DEFAULT_MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration(1, _SCHEMA_V1),
    Migration(2, _SCHEMA_V2),
    Migration(3, _SCHEMA_V3),
    Migration(4, _SCHEMA_V4),
    Migration(5, _SCHEMA_V5),
    Migration(6, _SCHEMA_V6),
    Migration(7, _SCHEMA_V7),
    Migration(8, _SCHEMA_V8),
    Migration(9, _SCHEMA_V9),
)


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
