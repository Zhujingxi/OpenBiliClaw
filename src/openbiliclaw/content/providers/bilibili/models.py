"""Strict provider-native Bilibili response and content schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter

from openbiliclaw.core._pydantic import StrictBaseModel


class Availability(StrEnum):
    AVAILABLE = "available"
    TOMBSTONE = "tombstone"


class BilibiliCreator(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=300)


class BilibiliStats(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    views: int = Field(ge=0)
    likes: int = Field(ge=0)
    favorites: int = Field(ge=0)


class BilibiliVideo(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["video"]
    id: str = Field(pattern=r"^BV[A-Za-z0-9]{6,20}$")
    aid: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(max_length=20_000)
    creator: BilibiliCreator | None
    cover_url: str | None = Field(pattern=r"^https?://[^\s]+$", max_length=2048)
    published_at: int = Field(ge=0)
    duration_seconds: int = Field(ge=0)
    stats: BilibiliStats
    availability: Availability


class BilibiliArticle(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["article"]
    id: str = Field(pattern=r"^cv[1-9][0-9]*$")
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(max_length=20_000)
    body: str = Field(min_length=1, max_length=100_000)
    creator: BilibiliCreator | None
    cover_url: str | None = Field(pattern=r"^https?://[^\s]+$", max_length=2048)
    published_at: int = Field(ge=0)
    stats: BilibiliStats
    availability: Availability


BilibiliItem = Annotated[BilibiliVideo | BilibiliArticle, Field(discriminator="kind")]
ITEM_ADAPTER: TypeAdapter[BilibiliItem] = TypeAdapter(BilibiliItem)


class BilibiliPageData(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[BilibiliItem, ...]
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)


class BilibiliNavData(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    is_login: bool
    mid: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)


class BilibiliActionData(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool


class BilibiliResponse(StrictBaseModel):
    """Generic strict API envelope used in fixture drift tests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: int
    message: str
    data: BilibiliPageData | BilibiliItem | BilibiliNavData | BilibiliActionData | None
