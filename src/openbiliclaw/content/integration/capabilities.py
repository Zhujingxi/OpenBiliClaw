"""Narrow typed provider capability contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, runtime_checkable

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from openbiliclaw.core._pydantic import StrictBaseModel

from .actions import ActionRequest, ActionResult

# Pydantic resolves these field types at runtime.
from .identity import ContentKind, ContentRef, ProviderId  # noqa: TC001

if TYPE_CHECKING:
    from openbiliclaw.access.models import AccessHandle

    from .native import NativeContent
    from .projections import CardData, ContentPreview, RecommendationCandidate, SearchDocument

ItemT = TypeVar("ItemT", bound=StrictBaseModel)
RequestT = TypeVar("RequestT", bound=ActionRequest, contravariant=True)
ResultT = TypeVar("ResultT", bound=ActionResult, covariant=True)


class ProviderCursor(StrictBaseModel):
    """Opaque cursor owned and interpreted only by its provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    value: str = Field(min_length=1, max_length=4096)


class PageRequest(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(default=20, ge=1, le=100)
    cursor: ProviderCursor | None = None


class ContentFilter(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kinds: frozenset[ContentKind] = frozenset()
    creator_id: str | None = Field(default=None, min_length=1, max_length=512)
    published_after: AwareDatetime | None = None
    published_before: AwareDatetime | None = None

    @model_validator(mode="after")
    def _time_order(self) -> ContentFilter:
        if (
            self.published_after is not None
            and self.published_before is not None
            and self.published_after > self.published_before
        ):
            raise ValueError("published_after must not exceed published_before")
        return self


class SearchQuery(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=1000)
    page: PageRequest = PageRequest()
    filters: ContentFilter = ContentFilter()


class FeedQuery(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feed_id: str | None = Field(default=None, min_length=1, max_length=512)
    page: PageRequest = PageRequest()
    filters: ContentFilter = ContentFilter()


class CreatorQuery(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    creator_id: str = Field(min_length=1, max_length=512)
    page: PageRequest = PageRequest()


class ContentPage(StrictBaseModel, Generic[ItemT]):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ItemT, ...]
    next_cursor: ProviderCursor | None


class ProviderObservation(StrictBaseModel):
    """Safe provider observation proposal; ingress owns persistence semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ContentRef
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    occurred_at: AwareDatetime


@runtime_checkable
class SearchCapability(Protocol):
    async def search(
        self, query: SearchQuery, access: AccessHandle
    ) -> ContentPage[ContentPreview]: ...


@runtime_checkable
class FeedCapability(Protocol):
    async def feed(self, query: FeedQuery, access: AccessHandle) -> ContentPage[ContentPreview]: ...


@runtime_checkable
class FetchCapability(Protocol):
    async def fetch(self, ref: ContentRef, access: AccessHandle) -> NativeContent: ...


@runtime_checkable
class RelatedCapability(Protocol):
    async def related(
        self, ref: ContentRef, page: PageRequest, access: AccessHandle
    ) -> ContentPage[ContentPreview]: ...


@runtime_checkable
class CreatorCapability(Protocol):
    async def creator(
        self, query: CreatorQuery, access: AccessHandle
    ) -> ContentPage[ContentPreview]: ...


@runtime_checkable
class HistoryCapability(Protocol):
    async def history(
        self, page: PageRequest, access: AccessHandle
    ) -> ContentPage[ContentPreview]: ...


@runtime_checkable
class SavedCapability(Protocol):
    async def saved(
        self, page: PageRequest, access: AccessHandle
    ) -> ContentPage[ContentPreview]: ...


@runtime_checkable
class ActionCapability(Protocol[RequestT, ResultT]):
    async def execute_action(self, request: RequestT, access: AccessHandle) -> ResultT: ...


@runtime_checkable
class ProjectionCapability(Protocol):
    def preview(self, content: NativeContent) -> ContentPreview: ...

    def recommendation_candidate(self, content: NativeContent) -> RecommendationCandidate: ...

    def search_document(self, content: NativeContent) -> SearchDocument: ...

    def card_data(self, content: NativeContent) -> CardData: ...


@runtime_checkable
class ObservationCapability(Protocol):
    def observations(self, content: NativeContent) -> tuple[ProviderObservation, ...]: ...
