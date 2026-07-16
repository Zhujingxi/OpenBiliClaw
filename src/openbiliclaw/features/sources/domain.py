"""Capabilities, concrete operations, and normalized source boundaries."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, TypeAlias, runtime_checkable
from uuid import UUID  # noqa: TC003 - Pydantic resolves this field at runtime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from openbiliclaw.features._metadata import FrozenMetadata, empty_metadata
from openbiliclaw.features.activity.domain import ActivityEvent  # noqa: TC001
from openbiliclaw.features.feed.domain import ContentItem  # noqa: TC001


class SourceCapability(StrEnum):
    """Stable product capabilities, independent of a provider's operation names."""

    AUTHENTICATION = "authentication"
    BOOTSTRAP_IMPORT = "bootstrap_import"
    ACTIVITY_COLLECTION = "activity_collection"
    SEARCH = "search"
    TRENDING_FEED = "trending_feed"
    RELATED_DISCOVERY = "related_discovery"
    CREATOR_DISCOVERY = "creator_discovery"
    COMMUNITY_DISCOVERY = "community_discovery"
    BROWSER_ASSISTED = "browser_assisted"


class SourceOperation(StrEnum):
    """Concrete read-only operations executable by source connectors."""

    BOOTSTRAP_IMPORT = "bootstrap_import"
    SEARCH = "search"
    TRENDING = "trending"
    FEED = "feed"
    RELATED = "related"
    CREATOR = "creator"
    COMMUNITY = "community"

    @property
    def requires_input(self) -> bool:
        return self in {
            SourceOperation.SEARCH,
            SourceOperation.RELATED,
            SourceOperation.CREATOR,
            SourceOperation.COMMUNITY,
        }


class SourceTransportKind(StrEnum):
    DIRECT = "direct"
    CLI = "cli"
    BROWSER = "browser"


class SourceResultKind(StrEnum):
    ACTIVITY = "activity"
    CONTENT = "content"


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
    """Raised instead of pretending a source can perform an absent operation."""


class SourceOperationSpec(BaseModel):
    """Execution metadata for one concrete operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: SourceOperation
    capability: SourceCapability
    result_kind: SourceResultKind
    requires_auth: bool
    transport_kind: SourceTransportKind
    fallback_transport_kind: SourceTransportKind | None = None

    @property
    def browser_assisted(self) -> bool:
        """Whether primary or fallback execution uses the durable browser queue."""

        return SourceTransportKind.BROWSER in {
            self.transport_kind,
            self.fallback_transport_kind,
        }


class SourceManifest(BaseModel):
    """Immutable source identity with separate capability and operation declarations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: SourceId
    display_name: str = Field(min_length=1, max_length=100)
    capabilities: frozenset[SourceCapability] = Field(min_length=1)
    operations: tuple[SourceOperationSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_operation_contract(self) -> SourceManifest:
        operation_ids = [spec.operation for spec in self.operations]
        if len(set(operation_ids)) != len(operation_ids):
            raise ValueError("source manifest operations must be unique")
        undeclared = {spec.capability for spec in self.operations} - self.capabilities
        if undeclared:
            raise ValueError("source operation capability must be declared")
        if any(spec.requires_auth for spec in self.operations) and (
            SourceCapability.AUTHENTICATION not in self.capabilities
        ):
            raise ValueError("authenticated operations require authentication capability")
        if any(spec.browser_assisted for spec in self.operations) and (
            SourceCapability.BROWSER_ASSISTED not in self.capabilities
        ):
            raise ValueError("browser operations require browser-assisted capability")
        return self

    def operation_spec(self, operation: SourceOperation) -> SourceOperationSpec:
        for spec in self.operations:
            if spec.operation is operation:
                return spec
        raise UnsupportedSourceOperationError(
            f"{self.source_id.value} does not support {operation.value}"
        )


SourceResult: TypeAlias = tuple[ActivityEvent, ...] | tuple[ContentItem, ...]


@runtime_checkable
class SourceConnector(Protocol):
    """Port implemented by source adapters without leaking transport payloads."""

    @property
    def manifest(self) -> SourceManifest: ...

    async def execute(
        self, operation: SourceOperation, query: str | None = None, limit: int = 20
    ) -> SourceResult: ...


class SourceTaskRequest(BaseModel):
    """Typed, secret-free source work persisted for a browser transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: SourceId
    operation: SourceOperation
    payload: FrozenMetadata = Field(default_factory=empty_metadata)


class ClaimedSourceTask(BaseModel):
    """One durable task leased to exactly one extension worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    source_id: SourceId
    operation: SourceOperation
    payload: FrozenMetadata = Field(default_factory=empty_metadata)
    lease_token: str = Field(min_length=20, max_length=100)
    lease_expires_at: AwareDatetime
    request_deadline_at: AwareDatetime


class SourceTaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


class SourceTaskSnapshot(BaseModel):
    """Read model used by an awaiting browser transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    status: SourceTaskStatus
    request_deadline_at: AwareDatetime
    result: FrozenMetadata | None = None


class SourceTaskCompletion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    completed_at: AwareDatetime
    idempotent: bool
