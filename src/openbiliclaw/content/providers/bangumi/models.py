"""Strict provider-native Bangumi schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class Availability(StrEnum):
    AVAILABLE = "available"
    TOMBSTONE = "tombstone"


class BangumiSubject(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(max_length=20_000)
    creator: str | None = Field(default=None, max_length=300)
    published_at: AwareDatetime
    subject_type: Literal["book", "anime", "music", "game", "real"]
    original_title: str = Field(max_length=500)
    image_url: str | None = Field(default=None, pattern=r"^https?://[^\s]+$", max_length=2048)
    score: float = Field(ge=0, le=10)
    rating_count: int = Field(ge=0)
    collection_count: int = Field(ge=0)
    availability: Availability


class BangumiPage(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[BangumiSubject, ...] = Field(max_length=50)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)
