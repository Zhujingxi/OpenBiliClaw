"""Strict provider-native V2EX schemas."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class Availability(StrEnum):
    AVAILABLE = "available"
    TOMBSTONE = "tombstone"


class V2EXMember(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    username: str = Field(min_length=1, max_length=128)


class V2EXNode(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)


class V2EXTopic(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=20_000)
    member: V2EXMember
    published_at: AwareDatetime
    node: V2EXNode
    reply_count: int = Field(ge=0)
    availability: Availability


class V2EXPage(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[V2EXTopic, ...] = Field(max_length=50)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)
