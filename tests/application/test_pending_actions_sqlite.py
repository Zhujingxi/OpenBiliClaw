from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from openbiliclaw.application.content_actions import PendingAction
from openbiliclaw.application.pending_actions import SqlitePendingActionRepository
from openbiliclaw.content.integration.actions import ActionResult
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_pending_action_survives_repository_restart(tmp_path: Path) -> None:
    path = tmp_path / "target.db"
    await SchemaMigrator(path).migrate()
    database = SqliteDatabase(path)
    await database.open()
    repository = SqlitePendingActionRepository(database)
    now = datetime.now(UTC)
    ref = ContentRef(
        provider_id=ProviderId(value="bilibili"),
        content_kind=ContentKind(value="video"),
        provider_content_id="BV1target",
        canonical_url="https://www.bilibili.com/video/BV1target",
    )
    pending = PendingAction(
        pending_action_id="pending_" + "1" * 32,
        idempotency_key="pending:test:1",
        action_id="save",
        ref=ref,
        user_id="local",
        safe_preview="Save this video",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    assert await repository.put(pending) == pending
    assert await repository.put(pending) == pending
    conflicting = pending.model_copy(update={"idempotency_key": "pending:conflict"})
    with pytest.raises(Exception, match="conflict"):
        await repository.put(conflicting)
    missing = pending.model_copy(update={"pending_action_id": "pending_" + "2" * 32})
    result = ActionResult(
        action_id="save",
        ref=ref,
        idempotency_key=pending.idempotency_key,
        completed_at=now,
    )
    with pytest.raises(Exception, match="not found"):
        await repository.complete(missing, result)
    completed = await repository.complete(pending, result)
    assert completed.result == result
    assert await repository.complete(pending, result) == completed
    await database.close()

    reopened = SqliteDatabase(path)
    await reopened.open()
    stored = await SqlitePendingActionRepository(reopened).get(pending.pending_action_id)
    assert stored is not None and stored.result == result
    await reopened.close()


@pytest.mark.asyncio
async def test_pending_completion_race_returns_concurrent_result() -> None:
    now = datetime.now(UTC)
    ref = ContentRef(
        provider_id=ProviderId(value="demo"),
        content_kind=ContentKind(value="video"),
        provider_content_id="1",
        canonical_url="https://example.com/1",
    )
    pending = PendingAction(
        pending_action_id="pending_" + "3" * 32,
        idempotency_key="pending:race",
        action_id="save",
        ref=ref,
        user_id="local",
        safe_preview="save",
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    result = ActionResult(
        action_id="save", ref=ref, idempotency_key=pending.idempotency_key, completed_at=now
    )
    concurrent = pending.model_copy(update={"result": result})

    class Session:
        async def execute(self, *_args: object) -> int:
            return 0

    class Database:
        @asynccontextmanager
        async def transaction(self):  # type: ignore[no-untyped-def]
            yield Session()

    repository = SqlitePendingActionRepository(cast("SqliteDatabase", Database()))
    calls = 0

    async def get(_identity: str) -> PendingAction | None:
        nonlocal calls
        calls += 1
        return pending if calls == 1 else concurrent

    repository.get = get  # type: ignore[assignment]
    assert await repository.complete(pending, result) == concurrent
