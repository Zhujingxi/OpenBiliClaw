"""Interactive chat use case with persisted turns and SSE-shaped output."""

from __future__ import annotations

import json
from contextlib import aclosing
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4  # noqa: TC003 - Pydantic resolves the field at runtime

from anyio import CapacityLimiter, to_thread
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind
from openbiliclaw.features.chat.domain import ChatHistoryTurn, ChatRole, ChatTurn

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from types import TracebackType

# Interactive chats should not serialize on SQLite, while a hard bound protects the
# process if clients disconnect during non-cancellable transaction cleanup.
_CHAT_PERSISTENCE_LIMITER = CapacityLimiter(8)


class ChatChunkKind(StrEnum):
    DELTA = "delta"
    DONE = "done"


class ChatChunk(BaseModel):
    """Transport-neutral chunk that can be rendered as an SSE event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ChatChunkKind
    content: str
    turn_id: UUID

    def to_sse(self) -> str:
        data = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        return f"event: {self.kind.value}\ndata: {data}\n\n"


class ChatResponseDelta(BaseModel):
    """One AI-run-associated delta from the interactive responder."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str = Field(min_length=1)
    ai_run_id: UUID


class ChatResponder(Protocol):
    """Interactive-lane adapter backed by the shared TaskRunner, never Huey."""

    def stream(
        self,
        *,
        conversation_id: UUID,
        message: str,
        history: tuple[ChatTurn, ...],
    ) -> AsyncGenerator[ChatResponseDelta]: ...


class ChatRepository(Protocol):
    def add(self, turn: ChatTurn) -> None: ...

    def list_by_conversation(
        self, conversation_id: UUID, *, limit: int, offset: int
    ) -> tuple[ChatTurn, ...]: ...

    def list_recent_by_conversation(
        self, conversation_id: UUID, *, limit: int
    ) -> tuple[ChatTurn, ...]: ...


class ActivityRepository(Protocol):
    def add(self, event: ActivityEvent) -> None: ...


class ChatUnitOfWork(Protocol):
    chat: ChatRepository
    activities: ActivityRepository

    def __enter__(self) -> ChatUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class ChatHistoryPage(BaseModel):
    """Stable bounded page of public turns for one conversation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: UUID
    items: tuple[ChatHistoryTurn, ...]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=1_000_000)
    has_more: bool

    @classmethod
    def from_turns(
        cls,
        *,
        conversation_id: UUID,
        turns: tuple[ChatTurn, ...],
        limit: int,
        offset: int,
        has_more: bool,
    ) -> ChatHistoryPage:
        return cls(
            conversation_id=conversation_id,
            items=tuple(ChatHistoryTurn.from_persisted(turn) for turn in turns),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )


class ChatService:
    """Persist both turns around one direct interactive TaskRunner adapter call."""

    def __init__(
        self,
        uow_factory: Callable[[], ChatUnitOfWork],
        *,
        responder: ChatResponder,
        history_limit: int = 30,
    ) -> None:
        if not 1 <= history_limit <= 100:
            raise ValueError("chat history limit must be between 1 and 100")
        self._uow_factory = uow_factory
        self._responder = responder
        self._history_limit = history_limit

    async def stream(
        self,
        *,
        conversation_id: UUID,
        message: str,
        learn: bool = False,
    ) -> AsyncGenerator[ChatChunk]:
        user_turn = ChatTurn(
            conversation_id=conversation_id,
            role=ChatRole.USER,
            content=message,
        )
        history = await to_thread.run_sync(
            self._load_history_and_persist_user_turn,
            user_turn,
            abandon_on_cancel=False,
            limiter=_CHAT_PERSISTENCE_LIMITER,
        )

        reply_parts: list[str] = []
        assistant_turn_id = uuid4()
        ai_run_id: UUID | None = None
        async with aclosing(
            self._responder.stream(
                conversation_id=conversation_id,
                message=message,
                history=history,
            )
        ) as deltas:
            async for delta in deltas:
                if ai_run_id is not None and delta.ai_run_id != ai_run_id:
                    raise RuntimeError("chat stream changed AI run identity")
                ai_run_id = delta.ai_run_id
                reply_parts.append(delta.content)
                yield ChatChunk(
                    kind=ChatChunkKind.DELTA,
                    content=delta.content,
                    turn_id=assistant_turn_id,
                )
        reply = "".join(reply_parts)
        if not reply.strip():
            raise ValueError("chat response cannot be empty")
        assistant_turn = ChatTurn(
            id=assistant_turn_id,
            conversation_id=conversation_id,
            role=ChatRole.ASSISTANT,
            content=reply,
            ai_run_id=ai_run_id,
        )
        await to_thread.run_sync(
            self._persist_assistant_turn,
            assistant_turn,
            learn,
            message,
            conversation_id,
            abandon_on_cancel=False,
            limiter=_CHAT_PERSISTENCE_LIMITER,
        )

        yield ChatChunk(kind=ChatChunkKind.DONE, content="", turn_id=assistant_turn.id)

    def history(
        self, *, conversation_id: UUID, limit: int = 50, offset: int = 0
    ) -> ChatHistoryPage:
        """Read one deterministic page while keeping persistence metadata private."""

        with self._uow_factory() as uow:
            turns = uow.chat.list_by_conversation(
                conversation_id,
                limit=limit + 1,
                offset=offset,
            )
        return ChatHistoryPage.from_turns(
            conversation_id=conversation_id,
            turns=turns[:limit],
            limit=limit,
            offset=offset,
            has_more=len(turns) > limit,
        )

    def _load_history_and_persist_user_turn(self, user_turn: ChatTurn) -> tuple[ChatTurn, ...]:
        with self._uow_factory() as uow:
            history = uow.chat.list_recent_by_conversation(
                user_turn.conversation_id,
                limit=self._history_limit,
            )
            uow.chat.add(user_turn)
            uow.commit()
        return history

    def _persist_assistant_turn(
        self,
        assistant_turn: ChatTurn,
        learn: bool,
        message: str,
        conversation_id: UUID,
    ) -> None:
        with self._uow_factory() as uow:
            uow.chat.add(assistant_turn)
            if learn:
                uow.activities.add(
                    ActivityEvent(
                        source_id="openbiliclaw",
                        kind=ActivityKind.CHAT_LEARNING,
                        text=message,
                        metadata={"conversation_id": str(conversation_id), "value": message},
                    )
                )
            uow.commit()


__all__ = [
    "ChatChunk",
    "ChatChunkKind",
    "ChatHistoryPage",
    "ChatResponseDelta",
    "ChatResponder",
    "ChatService",
]
