"""Capability declarations and normalized source connector/task boundaries."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID  # noqa: TC003 - Pydantic resolves this field at runtime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from openbiliclaw.features._metadata import FrozenMetadata, empty_metadata
from openbiliclaw.features.activity.domain import ActivityEvent  # noqa: TC001
from openbiliclaw.features.feed.domain import ContentItem  # noqa: TC001


class SourceCapability(StrEnum):
    """Operations a source may truthfully advertise."""

    ACTIVITY_IMPORT = "activity_import"
    SEARCH = "search"
    TRENDING = "trending"
    RELATED = "related"
    RECOMMENDED = "recommended"
    CREATOR = "creator"
    COMMUNITY = "community"
    EXPLORE = "explore"

    @property
    def requires_input(self) -> bool:
        """Whether dispatch needs a query, seed, creator, or community identifier."""

        return self in {
            SourceCapability.SEARCH,
            SourceCapability.RELATED,
            SourceCapability.CREATOR,
            SourceCapability.COMMUNITY,
        }


class SourceId(StrEnum):
    """Closed canonical identity set retained by the vNext product."""

    BILIBILI = "bilibili"
    XIAOHONGSHU = "xiaohongshu"
    DOUYIN = "douyin"
    YOUTUBE = "youtube"
    TWITTER = "twitter"
    ZHIHU = "zhihu"
    REDDIT = "reddit"


class UnsupportedSourceOperationError(ValueError):
    """Raised instead of pretending a source can perform an absent capability."""


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


class SourceTaskRequest(BaseModel):
    """Typed, secret-free source work persisted for a browser transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: SourceId
    operation: SourceCapability
    payload: FrozenMetadata = Field(default_factory=empty_metadata)


class ClaimedSourceTask(BaseModel):
    """One durable task leased to exactly one extension worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    source_id: SourceId
    operation: SourceCapability
    payload: FrozenMetadata = Field(default_factory=empty_metadata)
    lease_token: str = Field(min_length=20, max_length=100)
    lease_expires_at: AwareDatetime


class SourceTaskCompletion(BaseModel):
    """Completion acknowledgement, including duplicate callback classification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    completed_at: AwareDatetime
    idempotent: bool
