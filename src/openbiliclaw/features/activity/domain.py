"""Source-neutral activity evidence contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator


class ActivityKind(StrEnum):
    """Normalized user activities accepted by the vNext profile pipeline."""

    IMPORT = "import"
    VIEW = "view"
    DWELL = "dwell"
    LIKE = "like"
    FAVORITE = "favorite"
    SEARCH = "search"
    FOLLOW = "follow"
    FEEDBACK = "feedback"
    CHAT_LEARNING = "chat_learning"
    PROFILE_OVERRIDE = "profile_override"


class ActivityEvent(BaseModel):
    """Immutable evidence entering the product from any supported source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    source_id: str = Field(min_length=1, max_length=50)
    account_id: UUID | None = None
    kind: ActivityKind
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    content_external_id: str | None = Field(default=None, max_length=500)
    url: HttpUrl | None = None
    title: str | None = Field(default=None, max_length=1000)
    text: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProfileSignal(BaseModel):
    """A profile observation that always identifies its supporting evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    facet: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)
    weight: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    override: bool = False

    @model_validator(mode="before")
    @classmethod
    def give_user_overrides_full_confidence(cls, data: object) -> object:
        """Make explicit user statements authoritative at the contract boundary."""

        if isinstance(data, Mapping) and data.get("override") is True:
            return {**dict(data), "confidence": 1.0}
        return data
