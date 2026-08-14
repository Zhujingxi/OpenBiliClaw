"""Strict LinuxDo native schemas."""

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class LinuxDoItem(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^\d+$", max_length=20)
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(max_length=50_000)
    author: str = Field(max_length=300)
    url: str = Field(pattern=r"^https?://[^\s]+$", max_length=2048)
    published_at: int = Field(ge=0)
    deleted: bool = False


class LinuxDoPage(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[LinuxDoItem, ...]
    next_cursor: str | None = Field(default=None, min_length=1, max_length=4096)
