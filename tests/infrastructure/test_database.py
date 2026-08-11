from __future__ import annotations

import asyncio
import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase


async def test_connection_policy_transaction_rollback_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    db = SqliteDatabase(path, busy_timeout_ms=50)
    await db.open()
    async with db.transaction() as session:
        policy = await session.connection_policy()
        assert policy.foreign_keys is True
        assert policy.busy_timeout_ms == 50
        assert policy.journal_mode == "wal"
        await session.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        await session.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))")
    with pytest.raises(RuntimeError):
        async with db.transaction() as session:
            await session.execute("INSERT INTO parent(id) VALUES(?)", (1,))
            raise RuntimeError("rollback")
    assert await db.fetch_value("SELECT count(*) FROM parent") == 0
    async with db.transaction() as session:
        with pytest.raises(sqlite3.IntegrityError):
            await session.execute("INSERT INTO child(parent_id) VALUES(?)", (999,))
    await db.close()
    assert db.closed
    restarted = SqliteDatabase(path)
    await restarted.open()
    assert await restarted.fetch_value("SELECT count(*) FROM parent") == 0
    await restarted.close()


async def test_cross_connection_unique_write_race_has_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "race.db"
    first = SqliteDatabase(path)
    second = SqliteDatabase(path)
    await first.open()
    await second.open()
    await first.execute_script("CREATE TABLE values_(value TEXT UNIQUE NOT NULL);")

    async def insert(database: SqliteDatabase) -> bool:
        try:
            async with database.transaction() as session:
                await session.execute("INSERT INTO values_(value) VALUES(?)", ("same",))
            return True
        except sqlite3.IntegrityError:
            return False

    assert sorted(await asyncio.gather(insert(first), insert(second))) == [False, True]
    await first.close()
    await second.close()


async def test_execute_script_rolls_back_all_statements_on_failure(tmp_path: Path) -> None:
    db = SqliteDatabase(tmp_path / "script.db")
    await db.open()
    with pytest.raises(sqlite3.OperationalError):
        await db.execute_script(
            "CREATE TABLE first_table(value TEXT);"
            "INSERT INTO first_table VALUES('partial');"
            "INVALID SQL;"
        )
    assert (
        await db.fetch_value(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='first_table'"
        )
        == 0
    )
    await db.close()


async def test_busy_timeout_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "busy.db"
    db = SqliteDatabase(path, busy_timeout_ms=1)
    await db.open()
    await db.execute_script("CREATE TABLE values_(value TEXT);")
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("INSERT INTO values_ VALUES(?)", ("held",))
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            async with db.transaction() as session:
                await session.execute("INSERT INTO values_ VALUES(?)", ("blocked",))
    finally:
        blocker.rollback()
        blocker.close()
        await db.close()


async def test_cancellation_rolls_back_and_releases_session(tmp_path: Path) -> None:
    db = SqliteDatabase(tmp_path / "cancel.db")
    await db.open()
    await db.execute_script("CREATE TABLE values_(value TEXT);")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def work() -> None:
        async with db.transaction() as session:
            await session.execute("INSERT INTO values_ VALUES(?)", ("uncommitted",))
            entered.set()
            await release.wait()

    task = asyncio.create_task(work())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await db.fetch_value("SELECT count(*) FROM values_") == 0
    assert db.active_sessions == 0
    await db.close()


async def test_nested_transaction_and_use_after_close_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SqliteDatabase(tmp_path / "invalid.db", busy_timeout_ms=-1)
    db = SqliteDatabase(tmp_path / "state.db")
    with pytest.raises(RuntimeError, match="not open"):
        await db.fetch_value("SELECT 1")
    await db.open()
    await db.open()
    retained_session = None
    async with db.transaction() as session:
        retained_session = session
        assert await session.fetch_all("SELECT 1 UNION ALL SELECT 2") == ((1,), (2,))
        with pytest.raises(RuntimeError, match="nested"):
            async with db.transaction():
                pass
    assert retained_session is not None
    with pytest.raises(RuntimeError, match="session is closed"):
        await retained_session.fetch_one("SELECT 1")
    await db.close()
    await db.close()
    with pytest.raises(RuntimeError, match="closed"):
        await db.fetch_value("SELECT 1")
    with pytest.raises(RuntimeError, match="closed"):
        await db.open()
