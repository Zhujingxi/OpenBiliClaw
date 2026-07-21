"""Capabilities, concrete operations, and normalized source boundaries."""

from __future__ import annotations

import re
from collections.abc import Mapping as MappingABC
from enum import StrEnum
from typing import Annotated, Literal, Protocol, TypeAlias, TypedDict, runtime_checkable
from uuid import UUID  # noqa: TC003 - Pydantic resolves this field at runtime

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    model_validator,
)

from openbiliclaw.features._metadata import FrozenMetadata, empty_metadata, freeze_metadata
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
    request_schema: FrozenMetadata = Field(default_factory=empty_metadata)
    result_schema: FrozenMetadata = Field(default_factory=empty_metadata)

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
    settings_schema: FrozenMetadata = Field(default_factory=empty_metadata)
    credential_schema: FrozenMetadata = Field(default_factory=empty_metadata)

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


class SourceCredentialInput(BaseModel):
    """Write-only credential accepted when a source has a backend consumer."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    cookie: SecretStr = Field(
        min_length=1,
        max_length=16_384,
        json_schema_extra={"writeOnly": True},
    )


def source_form_schemas(
    settings_model: type[BaseModel],
) -> tuple[FrozenMetadata, FrozenMetadata]:
    """Derive safe generic-form descriptions without credential defaults or examples."""

    return (
        freeze_metadata(settings_model.model_json_schema()),
        freeze_metadata(SourceCredentialInput.model_json_schema()),
    )


class SourceFormSchemaFields(TypedDict):
    settings_schema: FrozenMetadata
    credential_schema: FrozenMetadata


def source_form_schema_fields(
    settings_model: type[BaseModel], *, accepts_credentials: bool = True
) -> SourceFormSchemaFields:
    """Return safe form fields, omitting credentials without a backend consumer."""

    settings_schema, credential_schema = source_form_schemas(settings_model)
    return {
        "settings_schema": settings_schema,
        "credential_schema": credential_schema if accepts_credentials else empty_metadata(),
    }


@runtime_checkable
class SourceConnector(Protocol):
    """Port implemented by source adapters without leaking transport payloads."""

    @property
    def manifest(self) -> SourceManifest: ...

    async def execute(
        self, operation: SourceOperation, query: str | None = None, limit: int = 20
    ) -> SourceResult: ...


class BrowserBootstrapRequest(BaseModel):
    """Request a bounded import of account activity visible to the source package."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation: Literal[SourceOperation.BOOTSTRAP_IMPORT]
    limit: int = Field(default=100, ge=1, le=100)


class BrowserSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation: Literal[SourceOperation.SEARCH]
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def normalize_query(self) -> BrowserSearchRequest:
        normalized = self.query.strip()
        if not normalized:
            raise ValueError("search query cannot be empty")
        object.__setattr__(self, "query", normalized)
        return self


class BrowserTrendingRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation: Literal[SourceOperation.TRENDING]
    limit: int = Field(default=20, ge=1, le=100)


class BrowserFeedRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation: Literal[SourceOperation.FEED]
    limit: int = Field(default=20, ge=1, le=100)


class BrowserRelatedRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation: Literal[SourceOperation.RELATED]
    seed: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def normalize_seed(self) -> BrowserRelatedRequest:
        normalized = self.seed.strip()
        if not normalized:
            raise ValueError("related seed cannot be empty")
        object.__setattr__(self, "seed", normalized)
        return self


class BrowserCreatorRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation: Literal[SourceOperation.CREATOR]
    creator: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def normalize_creator(self) -> BrowserCreatorRequest:
        normalized = self.creator.strip()
        if not normalized:
            raise ValueError("creator identifier cannot be empty")
        object.__setattr__(self, "creator", normalized)
        return self


class BrowserCommunityRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation: Literal[SourceOperation.COMMUNITY]
    community: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def normalize_community(self) -> BrowserCommunityRequest:
        normalized = self.community.strip()
        if not normalized:
            raise ValueError("community identifier cannot be empty")
        object.__setattr__(self, "community", normalized)
        return self


BrowserOperationRequest: TypeAlias = Annotated[
    BrowserBootstrapRequest
    | BrowserSearchRequest
    | BrowserTrendingRequest
    | BrowserFeedRequest
    | BrowserRelatedRequest
    | BrowserCreatorRequest
    | BrowserCommunityRequest,
    Field(discriminator="operation"),
]


class _BrowserOperationResultBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[FrozenMetadata, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def reject_credentials(cls, value: object) -> object:
        reject_credential_fields(value)
        return value


class BrowserBootstrapResult(_BrowserOperationResultBase):
    operation: Literal[SourceOperation.BOOTSTRAP_IMPORT]


class BrowserSearchResult(_BrowserOperationResultBase):
    operation: Literal[SourceOperation.SEARCH]


class BrowserTrendingResult(_BrowserOperationResultBase):
    operation: Literal[SourceOperation.TRENDING]


class BrowserFeedResult(_BrowserOperationResultBase):
    operation: Literal[SourceOperation.FEED]


class BrowserRelatedResult(_BrowserOperationResultBase):
    operation: Literal[SourceOperation.RELATED]


class BrowserCreatorResult(_BrowserOperationResultBase):
    operation: Literal[SourceOperation.CREATOR]


class BrowserCommunityResult(_BrowserOperationResultBase):
    operation: Literal[SourceOperation.COMMUNITY]


BrowserOperationResultValue: TypeAlias = Annotated[
    BrowserBootstrapResult
    | BrowserSearchResult
    | BrowserTrendingResult
    | BrowserFeedResult
    | BrowserRelatedResult
    | BrowserCreatorResult
    | BrowserCommunityResult,
    Field(discriminator="operation"),
]
BrowserOperationResult: TypeAdapter[BrowserOperationResultValue] = TypeAdapter(
    BrowserOperationResultValue
)


_REQUEST_MODELS: dict[SourceOperation, type[BaseModel]] = {
    SourceOperation.BOOTSTRAP_IMPORT: BrowserBootstrapRequest,
    SourceOperation.SEARCH: BrowserSearchRequest,
    SourceOperation.TRENDING: BrowserTrendingRequest,
    SourceOperation.FEED: BrowserFeedRequest,
    SourceOperation.RELATED: BrowserRelatedRequest,
    SourceOperation.CREATOR: BrowserCreatorRequest,
    SourceOperation.COMMUNITY: BrowserCommunityRequest,
}
_RESULT_MODELS: dict[SourceOperation, type[BaseModel]] = {
    SourceOperation.BOOTSTRAP_IMPORT: BrowserBootstrapResult,
    SourceOperation.SEARCH: BrowserSearchResult,
    SourceOperation.TRENDING: BrowserTrendingResult,
    SourceOperation.FEED: BrowserFeedResult,
    SourceOperation.RELATED: BrowserRelatedResult,
    SourceOperation.CREATOR: BrowserCreatorResult,
    SourceOperation.COMMUNITY: BrowserCommunityResult,
}


def browser_operation_schemas(
    operation: SourceOperation,
) -> tuple[FrozenMetadata, FrozenMetadata]:
    """Return stable schemas derived from the exact request/result Pydantic models."""

    return (
        freeze_metadata(_REQUEST_MODELS[operation].model_json_schema()),
        freeze_metadata(_RESULT_MODELS[operation].model_json_schema()),
    )


class SourceTaskRequest(BaseModel):
    """Typed, secret-free source work persisted for a browser transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: SourceId
    payload: BrowserOperationRequest

    @property
    def operation(self) -> SourceOperation:
        return SourceOperation(self.payload.operation)


class ClaimedSourceTask(BaseModel):
    """One durable task leased to exactly one extension worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    source_id: SourceId
    payload: BrowserOperationRequest
    lease_token: str = Field(min_length=20, max_length=100)
    lease_expires_at: AwareDatetime
    request_deadline_at: AwareDatetime

    @property
    def operation(self) -> SourceOperation:
        return SourceOperation(self.payload.operation)


class SourceTaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


class SourceTaskFailure(BaseModel):
    """Secret-free browser execution failure classification."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    code: Literal[
        "claim_mismatch",
        "operation_mismatch",
        "result_mismatch",
        "deadline_exceeded",
        "execution_failed",
    ]
    error_type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")


class SourceTaskSnapshot(BaseModel):
    """Read model used by an awaiting browser transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    operation: SourceOperation
    status: SourceTaskStatus
    request_deadline_at: AwareDatetime
    result: BrowserOperationResultValue | None = Field(default=None, discriminator="operation")
    failure: SourceTaskFailure | None = None


class SourceTaskCompletion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    completed_at: AwareDatetime
    idempotent: bool


class SourceAccountStatus(BaseModel):
    """Secret-free source-account status exposed to product clients."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: SourceId
    account_key: str = Field(min_length=1, max_length=200)
    configured: bool = True
    enabled: bool


class SourceAccountDisconnectResult(BaseModel):
    """Secret-free result for an idempotent account credential deletion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: SourceId
    account_key: str = Field(min_length=1, max_length=200)
    disconnected: Literal[True] = True
    idempotent: bool


class SourceSettingsState(BaseModel):
    """Safe persisted settings for one explicit built-in source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: SourceId
    settings: FrozenMetadata


_CREDENTIAL_TOKENS = frozenset(
    {
        "apikey",
        "apikeys",
        "authorization",
        "authorizations",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "passwords",
        "secret",
        "secrets",
        "session",
        "sessions",
        "token",
        "tokens",
    }
)
_CREDENTIAL_FIELD_SUFFIXES = (
    "apikey",
    "apikeys",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "passwords",
    "proxyauthorization",
    "secret",
    "secrets",
    "session",
    "sessions",
    "token",
    "tokens",
)
_SAFE_ANALYTICS_FIELDS = frozenset({"cookiepolicy", "sessionduration", "tokencount"})


class CredentialShapedPayloadError(ValueError):
    """Raised without values before credential-like browser data crosses a boundary."""


def reject_credential_fields(value: object, *, path: tuple[str, ...] = ()) -> None:
    """Reject nested credential-like keys while reporting names but never values."""

    if isinstance(value, MappingABC):
        for key, child in value.items():
            key_text = str(key)
            tokenized_key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key_text).casefold()
            normalized = re.sub(r"[^a-z0-9]", "", key_text.casefold())
            segments = frozenset(part for part in re.split(r"[^a-z0-9]+", tokenized_key) if part)
            sensitive = normalized not in _SAFE_ANALYTICS_FIELDS and (
                bool(segments & _CREDENTIAL_TOKENS)
                or normalized.endswith(_CREDENTIAL_FIELD_SUFFIXES)
            )
            if sensitive:
                safe_path = ".".join((*path, key_text))
                raise CredentialShapedPayloadError(
                    f"credential-shaped field is forbidden in source tasks: {safe_path}"
                )
            reject_credential_fields(child, path=(*path, key_text))
    elif isinstance(value, (list, tuple)):
        for child in value:
            reject_credential_fields(child, path=path)
