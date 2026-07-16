"""Local-only favorites and watch-later contracts."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class CollectionKind(StrEnum):
    """The two predefined local-only collections."""

    FAVORITES = "favorites"
    WATCH_LATER = "watch_later"


class CollectionItem(BaseModel):
    """A local collection membership for one normalized content item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    collection: CollectionKind
    content_id: UUID
    added_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    note: str = Field(default="", max_length=2000)
