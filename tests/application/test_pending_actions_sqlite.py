from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

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
    result = ActionResult(
        action_id="save",
        ref=ref,
        idempotency_key=pending.idempotency_key,
        completed_at=now,
    )
    assert (await repository.complete(pending, result)).result == result
    await database.close()

    reopened = SqliteDatabase(path)
    await reopened.open()
    stored = await SqlitePendingActionRepository(reopened).get(pending.pending_action_id)
    assert stored is not None and stored.result == result
    await reopened.close()
