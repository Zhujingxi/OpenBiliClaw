"""Model-free bounded application read workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import ConfigDict, Field

from openbiliclaw.access.forms import ConnectionForm  # noqa: TC001
from openbiliclaw.access.models import AccessStatus  # noqa: TC001
from openbiliclaw.content.integration.capabilities import (
    FetchCapability,
    PageRequest,
    SearchCapability,
    SearchQuery,
)
from openbiliclaw.content.integration.errors import (
    ContentIntegrationError,
    IntegrationErrorCode,
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


class GetSourceFormQuery(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str = Field(min_length=1, max_length=128)
    method_id: str = Field(min_length=1, max_length=128)


class SourceFormResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    form: ConnectionForm


class AccessFormReads(Protocol):
    def connection_forms(self, provider_id: str) -> tuple[ConnectionForm, ...]: ...


@dataclass(frozen=True, slots=True)
class GetSourceForm:
    access: AccessFormReads

    async def __call__(self, query: GetSourceFormQuery) -> SourceFormResult:
        form = next(
            (
                item
                for item in self.access.connection_forms(query.provider_id)
                if item.method_id == query.method_id
            ),
            None,
        )
        if form is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "connection form not found")
        return SourceFormResult(form=form)


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


@dataclass(frozen=True, slots=True)
class SearchContent:
    registry: ContentRegistryReads
    access: AccessReads

    async def __call__(self, query: SearchContentQuery) -> SearchContentResult:
        try:
            provider = self.registry.provider(query.provider_id)
            if not isinstance(provider, SearchCapability):
                raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "search unavailable")
            handle = self.access.connected_handle(query.provider_id.value, query.account_id)
            if handle is None:
                raise ApplicationError(ApplicationErrorCode.UNAUTHORIZED, "source is not connected")
            page = await provider.search(
                SearchQuery(text=query.text, page=PageRequest(limit=query.limit)), handle
            )
        except ContentIntegrationError as exc:
            raise _integration_error(exc) from exc
        return SearchContentResult(items=page.items)


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
        try:
            provider = self.registry.provider(query.ref.provider_id)
            if not isinstance(provider, FetchCapability):
                raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "fetch unavailable")
            handle = self.access.connected_handle(query.ref.provider_id.value, query.account_id)
            if handle is None:
                raise ApplicationError(ApplicationErrorCode.UNAUTHORIZED, "source is not connected")
            content = await provider.fetch(query.ref, handle)
        except ContentIntegrationError as exc:
            raise _integration_error(exc) from exc
        return ContentDetailsResult(content=content)


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


def _integration_error(error: ContentIntegrationError) -> ApplicationError:
    if error.code is IntegrationErrorCode.ACCESS_DENIED:
        code = ApplicationErrorCode.FORBIDDEN
    elif error.code is IntegrationErrorCode.INVALID_CONTENT_REF:
        code = ApplicationErrorCode.NOT_FOUND
    else:
        code = ApplicationErrorCode.UNAVAILABLE
    return ApplicationError(code, error.safe_message)


class JobHealthResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    health: HealthSnapshot


@dataclass(frozen=True, slots=True)
class GetJobHealth:
    source: HealthReads

    async def __call__(self) -> JobHealthResult:
        return JobHealthResult(health=self.source.health())
