"""Source-neutral content, assessment, feed, and interaction contracts."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
)


class ContentItem(BaseModel):
    """Normalized content identity and display metadata from any source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    source_id: str = Field(min_length=1, max_length=50)
    external_id: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    title: str = Field(min_length=1, max_length=1000)
    summary: str = ""
    creator: str | None = Field(default=None, max_length=500)
    published_at: AwareDatetime | None = None
    media_type: str = Field(default="link", min_length=1, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateAssessment(BaseModel):
    """Bounded profile-relative assessment of one content candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    content_id: UUID
    profile_revision: int = Field(ge=0)
    relevance: float
    quality: float
    novelty: float
    risk: float
    topics: tuple[str, ...] = ()
    explanation: str = ""

    @field_validator("relevance", "quality", "novelty", "risk", mode="before")
    @classmethod
    def clamp_score(cls, value: object) -> float:
        """Clamp finite numeric AI output to the public unit interval."""

        score = float(value)  # type: ignore[arg-type]
        if not math.isfinite(score):
            raise ValueError("assessment scores must be finite")
        return max(0.0, min(1.0, score))

    @property
    def score(self) -> float:
        """Return the deterministic admission score, bounded to 0..1."""

        weighted = (
            self.relevance * 0.5 + self.quality * 0.25 + self.novelty * 0.25 - self.risk * 0.5
        )
        return max(0.0, min(1.0, weighted))


class FeedEntry(BaseModel):
    """One admitted candidate in the user's ordered feed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    content_id: UUID
    assessment_id: UUID | None = None
    position: int = Field(ge=0)
    admitted_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    explanation: str = ""


class InteractionKind(StrEnum):
    """User actions that can affect ranking or local collections."""

    IMPRESSION = "impression"
    OPEN = "open"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    SAVE_FAVORITE = "save_favorite"
    SAVE_WATCH_LATER = "save_watch_later"
    DISMISS = "dismiss"


class Interaction(BaseModel):
    """An immutable user interaction with a normalized content item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    content_id: UUID
    kind: InteractionKind
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


def feed_deficit(current_unseen: int, low_watermark: int, high_watermark: int) -> int:
    """Replenish to the high watermark only below the low watermark."""

    if min(current_unseen, low_watermark, high_watermark) < 0:
        raise ValueError("feed watermarks cannot be negative")
    if low_watermark > high_watermark:
        raise ValueError("low watermark cannot exceed high watermark")
    if current_unseen >= low_watermark:
        return 0
    return high_watermark - current_unseen
