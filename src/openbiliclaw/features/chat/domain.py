"""Persisted chat contracts."""

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
