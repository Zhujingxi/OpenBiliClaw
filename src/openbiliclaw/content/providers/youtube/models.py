"""Strict provider-native YouTube schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class Availability(StrEnum):
    AVAILABLE = "available"
    TOMBSTONE = "tombstone"


class YouTubeChannel(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=300)


class YouTubeVideo(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(max_length=20_000)
    channel: YouTubeChannel | None
    published_at: AwareDatetime
    duration_seconds: int = Field(ge=0)
    view_count: int = Field(ge=0)
    thumbnail_url: str | None = Field(default=None, pattern=r"^https?://[^\s]+$", max_length=2048)
    availability: Availability


class YouTubePage(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[YouTubeVideo, ...] = Field(max_length=50)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)
