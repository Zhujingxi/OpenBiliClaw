from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from openbiliclaw.assistant.models import (
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationScope,
)
from openbiliclaw.assistant.repository import SqliteConversationRepository
from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase
from openbiliclaw.infrastructure.sqlite.schema import SchemaMigrator

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2030, 1, 1, tzinfo=UTC)


async def test_sqlite_conversation_restart_retention_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "assistant.db"
    await SchemaMigrator(path).migrate()
    db = SqliteDatabase(path)
    await db.open()
    repo = SqliteConversationRepository(db)
    conversation = Conversation(
        conversation_id="conv_" + "a" * 32,
        scope=ConversationScope(local_user_id="local", device_id="desktop"),
        created_at=NOW,
        updated_at=NOW,
        retention_days=30,
    )
    message = ConversationMessage(
        message_id="msg_" + "b" * 32,
        role=ConversationRole.USER,
        content="hello",
        created_at=NOW,
        idempotency_key="turn:00000001",
    )
    await repo.put_conversation(conversation)
    assert await repo.append_message(conversation.conversation_id, message)
    assert not await repo.append_message(conversation.conversation_id, message)
    await db.close()

    restarted = SqliteDatabase(path)
    await restarted.open()
    repo2 = SqliteConversationRepository(restarted)
    loaded = await repo2.get_conversation(conversation.conversation_id, conversation.scope)
    assert loaded == conversation
    assert await repo2.messages(conversation.conversation_id, limit=10) == (message,)
    assert await repo2.all_messages(conversation.conversation_id) == (message,)
    assert (
        await repo2.get_conversation(
            conversation.conversation_id,
            ConversationScope(local_user_id="other", device_id="desktop"),
        )
        is None
    )
    assert await repo2.purge_expired(NOW + timedelta(days=31)) == 1
    assert await repo2.messages(conversation.conversation_id, limit=10) == ()

    await repo2.put_conversation(conversation)
    assert await repo2.delete(conversation.conversation_id, conversation.scope)
    assert not await repo2.delete(conversation.conversation_id, conversation.scope)
    await restarted.close()
