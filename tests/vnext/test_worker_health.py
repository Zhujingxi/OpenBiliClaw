from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

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
