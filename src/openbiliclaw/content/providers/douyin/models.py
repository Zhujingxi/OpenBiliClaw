"""Strict provider-native schemas for replayable Douyin public responses."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class DouyinAuthor(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sec_uid: str = Field(min_length=1, max_length=256)
    nickname: str = Field(min_length=1, max_length=300)


class DouyinVideoMedia(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cover_url: str | None = Field(pattern=r"^https?://[^\s]+$", max_length=2048)
    duration_ms: int = Field(ge=0)


class DouyinStatistics(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    play_count: int = Field(ge=0)
    digg_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)
    collect_count: int = Field(ge=0)
    share_count: int = Field(ge=0)


class DouyinAweme(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    aweme_id: str = Field(pattern=r"^[0-9]{10,32}$")
    desc: str = Field(min_length=1, max_length=20_000)
    author: DouyinAuthor | None
    video: DouyinVideoMedia | None
    statistics: DouyinStatistics | None
    create_time: int = Field(ge=0)
    availability: Literal["available", "tombstone"]


class DouyinResponse(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status_code: int
    items: tuple[DouyinAweme, ...] = Field(max_length=50)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)
