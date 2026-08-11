"""Async SQLite connection and explicit transaction primitives."""

from __future__ import annotations

import asyncio
import contextvars
import sqlite3
from collections.abc import AsyncIterator, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from pathlib import Path

SqlValue = int | float | str | bytes | None
SqlParameters = Sequence[SqlValue]
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ConnectionPolicy:
    """Effective policy for a transaction connection."""

    foreign_keys: bool
    busy_timeout_ms: int
    journal_mode: str


class SqliteSession:
    """A transaction-scoped SQLite session with no commit operation."""

    def __init__(self, database: SqliteDatabase, connection: sqlite3.Connection) -> None:
        self._database = database
        self._connection = connection
        self._active = True

    async def execute(self, sql: str, parameters: SqlParameters = ()) -> int:
        """Execute parameterized SQL and return affected rows."""

        self._require_active()

        def operation() -> int:
            cursor = self._connection.execute(sql, tuple(parameters))
            return cursor.rowcount

        return await self._database._submit(operation)

    async def fetch_one(
        self, sql: str, parameters: SqlParameters = ()
    ) -> tuple[SqlValue, ...] | None:
        """Return one typed row."""

        self._require_active()

        def operation() -> tuple[SqlValue, ...] | None:
            row = self._connection.execute(sql, tuple(parameters)).fetchone()
            return tuple(row) if row is not None else None

        return await self._database._submit(operation)

    async def fetch_all(
        self, sql: str, parameters: SqlParameters = ()
    ) -> tuple[tuple[SqlValue, ...], ...]:
        """Return typed rows without leaking sqlite row objects."""

        self._require_active()

        def operation() -> tuple[tuple[SqlValue, ...], ...]:
            rows = self._connection.execute(sql, tuple(parameters)).fetchall()
            return tuple(tuple(row) for row in rows)

        return await self._database._submit(operation)

    async def connection_policy(self) -> ConnectionPolicy:
        """Inspect the effective safety pragmas."""

        foreign_keys = await self.fetch_one("PRAGMA foreign_keys")
        timeout = await self.fetch_one("PRAGMA busy_timeout")
        journal = await self.fetch_one("PRAGMA journal_mode")
        timeout_value = timeout[0] if timeout is not None else 0
        if not isinstance(timeout_value, int):
            raise RuntimeError("SQLite returned an invalid busy timeout")
        return ConnectionPolicy(
            foreign_keys=foreign_keys == (1,),
            busy_timeout_ms=timeout_value,
            journal_mode=str(journal[0]).lower() if journal is not None else "",
        )

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError("SQLite session is closed")

    def _deactivate(self) -> None:
        self._active = False


class SqliteDatabase:
    """SQLite lifecycle and one controlled blocking-executor boundary."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self._path = path
        self._busy_timeout_ms = busy_timeout_ms
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openbiliclaw-sqlite")
        # ponytail: one lock serializes this database instance; use per-session executors only if
        # measured transaction throughput requires concurrent connections.
        self._transaction_lock = asyncio.Lock()
        self._in_transaction: contextvars.ContextVar[bool] = contextvars.ContextVar(
            f"sqlite-transaction-{id(self)}", default=False
        )
        self._opened = False
        self._closed = False
        self._active_sessions = 0

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def active_sessions(self) -> int:
        return self._active_sessions

    async def open(self) -> None:
        """Create the file and verify the configured connection policy."""

        if self._closed:
            raise RuntimeError("SQLite database is closed")
        if self._opened:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = await self._submit(self._new_connection)
        await self._submit(connection.close)
        self._opened = True

    async def close(self) -> None:
        """Wait for an active transaction, then stop the executor."""

        if self._closed:
            return
        async with self._transaction_lock:
            self._closed = True
            self._opened = False
        self._executor.shutdown(wait=True, cancel_futures=True)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SqliteSession]:
        """Open an explicit atomic transaction; rollback on every exception/cancellation."""

        self._require_open()
        if self._in_transaction.get():
            raise RuntimeError("nested transactions are forbidden")
        async with self._transaction_lock:
            self._require_open()
            token = self._in_transaction.set(True)
            try:
                connection = await self._submit(self._new_connection)
            except BaseException:
                self._in_transaction.reset(token)
                raise
            session = SqliteSession(self, connection)
            self._active_sessions += 1
            try:
                await self._submit(lambda: connection.execute("BEGIN IMMEDIATE"))
                yield session
                await self._submit(connection.commit)
            except BaseException:
                await asyncio.shield(self._submit(connection.rollback))
                raise
            finally:
                session._deactivate()
                await asyncio.shield(self._submit(connection.close))
                self._active_sessions -= 1
                self._in_transaction.reset(token)

    async def fetch_value(self, sql: str, parameters: SqlParameters = ()) -> SqlValue:
        """Read a scalar in a short transaction."""

        async with self.transaction() as session:
            row = await session.fetch_one(sql, parameters)
            return row[0] if row else None

    async def execute_script(self, script: str) -> None:
        """Execute trusted schema SQL in a short transaction."""

        async with self.transaction() as session:
            atomic_script = f"BEGIN IMMEDIATE;\n{script}\nCOMMIT;"
            await self._submit(lambda: session._connection.executescript(atomic_script))

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            isolation_level=None,
            check_same_thread=True,
            timeout=self._busy_timeout_ms / 1000,
        )
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    async def _submit(self, operation: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, operation)

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("SQLite database is closed")
        if not self._opened:
            raise RuntimeError("SQLite database is not open")
