"""Strict provider-native Hacker News schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class HackerNewsItemType(StrEnum):
    STORY = "story"
    JOB = "job"
    POLL = "poll"


class HackerNewsItem(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(gt=0)
    item_type: HackerNewsItemType
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(max_length=20_000)
    author: str | None = Field(default=None, min_length=1, max_length=128)
    published_at: AwareDatetime
    score: int = Field(ge=0, le=2_147_483_647)
    comment_count: int = Field(ge=0, le=2_147_483_647)
    external_url: str | None = Field(default=None, pattern=r"^https?://[^\s]+$", max_length=2048)


class HackerNewsPage(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[HackerNewsItem, ...] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)
