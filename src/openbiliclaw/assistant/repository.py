"""Assistant-owned conversation repository port and SQLite adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .models import Conversation, ConversationMessage, ConversationScope

if TYPE_CHECKING:
    from datetime import datetime

    from openbiliclaw.infrastructure.sqlite.database import SqliteDatabase


class ConversationRepository(Protocol):
    async def put_conversation(self, conversation: Conversation) -> None: ...
    async def get_conversation(
        self, conversation_id: str, scope: ConversationScope
    ) -> Conversation | None: ...
    async def append_message(self, conversation_id: str, message: ConversationMessage) -> bool: ...
    async def messages(
        self, conversation_id: str, *, limit: int
    ) -> tuple[ConversationMessage, ...]: ...
    async def all_messages(self, conversation_id: str) -> tuple[ConversationMessage, ...]: ...
    async def purge_expired(self, now: datetime) -> int: ...
    async def delete(self, conversation_id: str, scope: ConversationScope) -> bool: ...


class SqliteConversationRepository:
    """Typed JSON-at-boundary adapter over the target Assistant tables."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database

    async def put_conversation(self, conversation: Conversation) -> None:
        async with self._database.transaction() as session:
            await session.execute(
                "INSERT INTO assistant_conversations("
                "conversation_id,created_at,updated_at,conversation_json) VALUES(?,?,?,?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET "
                "updated_at=excluded.updated_at,conversation_json=excluded.conversation_json",
                (
                    conversation.conversation_id,
                    conversation.created_at.isoformat(),
                    conversation.updated_at.isoformat(),
                    conversation.model_dump_json(),
                ),
            )

    async def get_conversation(
        self, conversation_id: str, scope: ConversationScope
    ) -> Conversation | None:
        async with self._database.transaction() as session:
            row = await session.fetch_one(
                "SELECT conversation_json FROM assistant_conversations WHERE conversation_id=?",
                (conversation_id,),
            )
        if row is None or not isinstance(row[0], str):
            return None
        conversation = Conversation.model_validate_json(row[0])
        return conversation if conversation.scope == scope else None

    async def append_message(self, conversation_id: str, message: ConversationMessage) -> bool:
        async with self._database.transaction() as session:
            changed = await session.execute(
                "INSERT OR IGNORE INTO assistant_messages("
                "message_id,conversation_id,role,content_json,created_at,idempotency_key"
                ") VALUES(?,?,?,?,?,?)",
                (
                    message.message_id,
                    conversation_id,
                    message.role.value,
                    message.model_dump_json(),
                    message.created_at.isoformat(),
                    message.idempotency_key,
                ),
            )
        return changed == 1

    async def messages(
        self, conversation_id: str, *, limit: int
    ) -> tuple[ConversationMessage, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("message limit must be between 1 and 100")
        async with self._database.transaction() as session:
            rows = await session.fetch_all(
                "SELECT content_json FROM assistant_messages WHERE conversation_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (conversation_id, limit),
            )
        parsed = tuple(
            ConversationMessage.model_validate_json(row[0])
            for row in rows
            if isinstance(row[0], str)
        )
        return tuple(reversed(parsed))

    async def all_messages(self, conversation_id: str) -> tuple[ConversationMessage, ...]:
        """Return the full persisted transcript for model-window selection."""

        async with self._database.transaction() as session:
            rows = await session.fetch_all(
                "SELECT content_json FROM assistant_messages WHERE conversation_id=? "
                "ORDER BY created_at",
                (conversation_id,),
            )
        return tuple(
            ConversationMessage.model_validate_json(row[0])
            for row in rows
            if isinstance(row[0], str)
        )

    async def purge_expired(self, now: datetime) -> int:
        async with self._database.transaction() as session:
            rows = await session.fetch_all(
                "SELECT conversation_id,conversation_json FROM assistant_conversations"
            )
            expired: list[str] = []
            for conversation_id, payload in rows:
                if not isinstance(conversation_id, str) or not isinstance(payload, str):
                    raise RuntimeError("invalid assistant conversation row")
                conversation = Conversation.model_validate_json(payload)
                if (now - conversation.updated_at).days >= conversation.retention_days:
                    expired.append(conversation_id)
            for conversation_id in expired:
                await session.execute(
                    "DELETE FROM assistant_conversations WHERE conversation_id=?",
                    (conversation_id,),
                )
        return len(expired)

    async def delete(self, conversation_id: str, scope: ConversationScope) -> bool:
        conversation = await self.get_conversation(conversation_id, scope)
        if conversation is None:
            return False
        async with self._database.transaction() as session:
            changed = await session.execute(
                "DELETE FROM assistant_conversations WHERE conversation_id=?", (conversation_id,)
            )
        return changed == 1
