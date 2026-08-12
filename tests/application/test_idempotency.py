from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.application.idempotency import SqliteIdempotencyJournal
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_sqlite_idempotency_survives_repository_reconstruction(tmp_path: Path) -> None:
    path = tmp_path / "target.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    first = SqliteIdempotencyJournal(database)
    assert await first.get("connect:one") is None
    await first.put("connect:one", '{"status":"done"}')
    await first.put("connect:one", '{"status":"other"}')
    second = SqliteIdempotencyJournal(database)
    assert await second.get("connect:one") == '{"status":"done"}'
    await database.close()
