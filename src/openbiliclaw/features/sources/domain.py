"""Capability declarations and the normalized source connector boundary."""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.features.activity.domain import ActivityEvent
from openbiliclaw.features.feed.domain import ContentItem


class SourceCapability(StrEnum):
    """Operations a source may truthfully advertise."""

    ACTIVITY_IMPORT = "activity_import"
    SEARCH = "search"
    TRENDING = "trending"
    RELATED = "related"
    RECOMMENDED = "recommended"


class SourceManifest(BaseModel):
    """Immutable identity and capability declaration for one source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=100)
    capabilities: frozenset[SourceCapability] = Field(min_length=1)
    requires_account: bool = False


@runtime_checkable
class SourceConnector(Protocol):
    """Port implemented by source adapters without leaking transport payloads."""

    @property
    def manifest(self) -> SourceManifest:
        """Return this connector's stable identity and supported operations."""

        ...

    async def import_activity(self) -> tuple[ActivityEvent, ...]:
        """Return normalized activity or reject the unsupported operation."""

        ...

    async def discover(
        self, capability: SourceCapability, query: str | None, limit: int
    ) -> tuple[ContentItem, ...]:
        """Return normalized content for a supported discovery capability."""

        ...
