"""Model-free bounded application read workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import ConfigDict, Field

from openbiliclaw.access.models import AccessStatus  # noqa: TC001
from openbiliclaw.content.integration.capabilities import (
    FetchCapability,
    PageRequest,
    SearchCapability,
    SearchQuery,
)
from openbiliclaw.content.integration.identity import ContentRef, ProviderId  # noqa: TC001
from openbiliclaw.content.integration.native import NativeContent  # noqa: TC001
from openbiliclaw.content.integration.projections import ContentPreview  # noqa: TC001
from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.core.health import HealthSnapshot  # noqa: TC001
from openbiliclaw.recommendation.models import SelectionRecord  # noqa: TC001
from openbiliclaw.understanding.profile import CanonicalProfile  # noqa: TC001
from openbiliclaw.understanding.projections import (
    DialogueProfile,
    dialogue_projection,
)

from .errors import ApplicationError, ApplicationErrorCode

if TYPE_CHECKING:
    from openbiliclaw.access.models import AccessHandle


class AccessReads(Protocol):
    async def status(self, provider_id: str, account_id: str | None) -> AccessStatus: ...
    def connected_handle(self, provider_id: str, account_id: str | None) -> AccessHandle | None: ...


class RecommendationReads(Protocol):
    async def feed(self, *, limit: int) -> tuple[SelectionRecord, ...]: ...


class ContentRegistryReads(Protocol):
    def provider(self, provider_id: ProviderId) -> object: ...


class UnderstandingReads(Protocol):
    async def profile(self, profile_id: str) -> CanonicalProfile: ...


class HealthReads(Protocol):
    def health(self) -> HealthSnapshot: ...


class GetSourceStatusQuery(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str = Field(min_length=1, max_length=128)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)


class SourceStatusResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: AccessStatus


@dataclass(frozen=True, slots=True)
class GetSourceStatus:
    access: AccessReads

    async def __call__(self, query: GetSourceStatusQuery) -> SourceStatusResult:
        return SourceStatusResult(
            status=await self.access.status(query.provider_id, query.account_id)
        )


class ListSourcesQuery(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=50, ge=1, le=100)


class SourcesResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[AccessStatus, ...]


@dataclass(frozen=True, slots=True)
class ListSources:
    provider_ids: tuple[str, ...]
    access: AccessReads

    async def __call__(self, query: ListSourcesQuery) -> SourcesResult:
        ids = self.provider_ids[: query.limit]
        return SourcesResult(
            items=tuple(
                [await self.access.status(provider_id, query.account_id) for provider_id in ids]
            )
        )


class GetRecommendationsQuery(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    limit: int = Field(default=20, ge=1, le=100)


class RecommendationsResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[SelectionRecord, ...]


@dataclass(frozen=True, slots=True)
class GetRecommendations:
    service: RecommendationReads

    async def __call__(self, query: GetRecommendationsQuery) -> RecommendationsResult:
        return RecommendationsResult(items=await self.service.feed(limit=query.limit))


class SearchContentQuery(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: ProviderId
    text: str = Field(min_length=1, max_length=1000)
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=20, ge=1, le=50)


class SearchContentResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[ContentPreview, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class SearchContent:
    registry: ContentRegistryReads
    access: AccessReads

    async def __call__(self, query: SearchContentQuery) -> SearchContentResult:
        provider = self.registry.provider(query.provider_id)
        if not isinstance(provider, SearchCapability):
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "search unavailable")
        handle = self.access.connected_handle(query.provider_id.value, query.account_id)
        if handle is None:
            raise ApplicationError(ApplicationErrorCode.UNAUTHORIZED, "source is not connected")
        page = await provider.search(
            SearchQuery(text=query.text, page=PageRequest(limit=query.limit)), handle
        )
        return SearchContentResult(
            items=page.items,
            next_cursor=page.next_cursor.value if page.next_cursor is not None else None,
        )


class GetContentDetailsQuery(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: ContentRef
    account_id: str | None = Field(default=None, min_length=1, max_length=128)


class ContentDetailsResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content: NativeContent


@dataclass(frozen=True, slots=True)
class GetContentDetails:
    registry: ContentRegistryReads
    access: AccessReads

    async def __call__(self, query: GetContentDetailsQuery) -> ContentDetailsResult:
        provider = self.registry.provider(query.ref.provider_id)
        if not isinstance(provider, FetchCapability):
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "fetch unavailable")
        handle = self.access.connected_handle(query.ref.provider_id.value, query.account_id)
        if handle is None:
            raise ApplicationError(ApplicationErrorCode.UNAUTHORIZED, "source is not connected")
        return ContentDetailsResult(content=await provider.fetch(query.ref, handle))


class ShowProfileQuery(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: str = Field(min_length=1, max_length=128)


class ProfileResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile: DialogueProfile


@dataclass(frozen=True, slots=True)
class ShowProfile:
    understanding: UnderstandingReads

    async def __call__(self, query: ShowProfileQuery) -> ProfileResult:
        profile = await self.understanding.profile(query.profile_id)
        return ProfileResult(profile=dialogue_projection(profile))


class JobHealthResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    health: HealthSnapshot


@dataclass(frozen=True, slots=True)
class GetJobHealth:
    source: HealthReads

    async def __call__(self) -> JobHealthResult:
        return JobHealthResult(health=self.source.health())
