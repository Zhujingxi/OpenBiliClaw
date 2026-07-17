"""Persisted chat contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ChatRole(StrEnum):
    """Roles persisted in a chat conversation."""

    USER = "user"
    ASSISTANT = "assistant"


class ChatTurn(BaseModel):
    """One immutable persisted turn in a conversation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    role: ChatRole
    content: str = Field(min_length=1)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    ai_run_id: UUID | None = None


class ChatHistoryTurn(BaseModel):
    """Public chat turn projection without AI/provider execution metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    role: ChatRole
    content: str
    created_at: AwareDatetime

    @classmethod
    def from_persisted(cls, turn: ChatTurn) -> ChatHistoryTurn:
        return cls(
            id=turn.id,
            role=turn.role,
            content=turn.content,
            created_at=turn.created_at,
        )
