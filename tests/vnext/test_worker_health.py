from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from openbiliclaw.infrastructure.database import operations
from openbiliclaw.infrastructure.jobs.health import worker_health_ready

ROOT = Path(__file__).resolve().parents[2]
WORKER_CMDLINE = b"python\x00-m\x00openbiliclaw.worker\x00"


def _database_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _migrate(path: Path) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", _database_url(path))
    command.upgrade(config, "head")


def test_worker_health_requires_process_schema_head_and_queue_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "openbiliclaw.db"
    queue = tmp_path / "huey.db"
    _migrate(database)
    with sqlite3.connect(queue) as connection:
        connection.execute("CREATE TABLE queue_probe (id INTEGER PRIMARY KEY)")

    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", _database_url(database))
    monkeypatch.setenv("OPENBILICLAW_HUEY_PATH", str(queue))
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))

    assert worker_health_ready(process_cmdline=WORKER_CMDLINE)
    assert not worker_health_ready(process_cmdline=b"python\x00-m\x00http.server\x00")
    assert not worker_health_ready(process_cmdline=b"echo\x00openbiliclaw.worker\x00")
    with sqlite3.connect(queue) as connection:
        assert connection.execute("SELECT count(*) FROM queue_probe").fetchone() == (0,)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_schema WHERE name LIKE '__openbiliclaw_write_probe_%'"
            ).fetchall()
            == []
        )

    queue.write_bytes(b"not sqlite")
    assert not worker_health_ready(process_cmdline=WORKER_CMDLINE)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits required")
def test_worker_health_rejects_read_only_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "openbiliclaw.db"
    queue = tmp_path / "huey.db"
    _migrate(database)
    with sqlite3.connect(queue) as connection:
        connection.execute("CREATE TABLE queue_probe (id INTEGER PRIMARY KEY)")
    queue.chmod(0o400)

    monkeypatch.setenv("OPENBILICLAW_DATABASE_URL", _database_url(database))
    monkeypatch.setenv("OPENBILICLAW_HUEY_PATH", str(queue))
    monkeypatch.setenv("OPENBILICLAW_ALEMBIC_INI", str(ROOT / "alembic.ini"))

    try:
        assert not worker_health_ready(process_cmdline=WORKER_CMDLINE)
    finally:
        queue.chmod(0o600)


def test_queue_writability_executes_mutation_then_rolls_it_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    queue = tmp_path / "huey.db"
    sqlite3.connect(queue).close()
    original_connect = operations.sqlite3.connect
    statements: list[str] = []

    class RecordingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def __enter__(self) -> RecordingConnection:
            self.connection.__enter__()
            return self

        def __exit__(self, *args: object) -> None:
            self.connection.__exit__(*args)

        def execute(self, statement: str) -> sqlite3.Cursor:
            statements.append(statement)
            return self.connection.execute(statement)

    def record_connect(*args: object, **kwargs: object) -> RecordingConnection:
        return RecordingConnection(original_connect(*args, **kwargs))

    monkeypatch.setattr(operations.sqlite3, "connect", record_connect)

    assert operations._write_transaction_available(queue)
    assert any(statement.startswith("CREATE TABLE") for statement in statements)
    assert any(statement.startswith("INSERT INTO") for statement in statements)
    assert statements[-1] == "ROLLBACK"
    with original_connect(queue) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_schema WHERE name LIKE '__openbiliclaw_write_probe_%'"
            ).fetchall()
            == []
        )


def test_queue_writability_rejects_path_replacement_after_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    queue = tmp_path / "huey.db"
    original = tmp_path / "original.db"
    replacement = tmp_path / "replacement.db"
    sqlite3.connect(queue).close()
    sqlite3.connect(replacement).close()
    original_connect = operations.sqlite3.connect

    class ReplacingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def __enter__(self) -> sqlite3.Connection:
            return self.connection.__enter__()

        def __exit__(self, *args: object) -> None:
            self.connection.__exit__(*args)
            queue.rename(original)
            replacement.rename(queue)

    def replace_after_connect(*args: object, **kwargs: object) -> ReplacingConnection:
        return ReplacingConnection(original_connect(*args, **kwargs))

    monkeypatch.setattr(operations.sqlite3, "connect", replace_after_connect)

    assert not operations._write_transaction_available(queue)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes required")
def test_queue_writability_rejects_unwritable_journal_directory(tmp_path: Path) -> None:
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    queue = queue_dir / "huey.db"
    with sqlite3.connect(queue) as connection:
        connection.execute("CREATE TABLE existing (value INTEGER NOT NULL)")
    queue.chmod(0o600)
    queue_dir.chmod(0o500)

    try:
        assert not operations._write_transaction_available(queue)
    finally:
        queue_dir.chmod(0o700)
