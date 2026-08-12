"""Strict provider-native schemas for RedNote note envelopes."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class RednoteAuthor(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=256)
    nickname: str = Field(min_length=1, max_length=300)


class RednoteNote(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    note_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(max_length=20_000)
    author: RednoteAuthor | None
    cover_url: str | None = Field(pattern=r"^https?://[^\s]+$", max_length=2048)
    published_at: int = Field(ge=0)
    likes: int = Field(ge=0)
    availability: Literal["available", "tombstone"]


class RednoteResponse(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: int
    items: tuple[RednoteNote, ...] = Field(max_length=50)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)
