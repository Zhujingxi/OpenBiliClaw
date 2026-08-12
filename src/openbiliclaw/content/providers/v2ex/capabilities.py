"""Narrow V2EX capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.access.models import AccessHandle, Permission
from openbiliclaw.content.integration.capabilities import (
    ContentPage,
    CreatorQuery,
    FeedQuery,
    PageRequest,
    ProviderCursor,
    SearchQuery,
)
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ContentRef
from openbiliclaw.content.integration.native import NativeContent

from . import projections
from .manifest import TOPIC_KIND, V2EX_ID
from .models import V2EXTopic

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.content.integration.projections import (
        CardData,
        ContentPreview,
        RecommendationCandidate,
        SearchDocument,
    )

    from .client import V2EXClient


class V2EXProvider:
    _MAX_ITEMS = 50

    def __init__(self, client: V2EXClient) -> None:
        self._client = client

    async def search(self, query: SearchQuery, access: AccessHandle) -> ContentPage[ContentPreview]:
        self._access(access)
        return await self._page("search", query.text, query.page)

    async def feed(self, query: FeedQuery, access: AccessHandle) -> ContentPage[ContentPreview]:
        self._access(access)
        return await self._page("feed", query.feed_id or "hot", query.page)

    async def fetch(self, ref: ContentRef, access: AccessHandle) -> NativeContent:
        self._access(access)
        self._ref(ref)
        page = await self._client.page("fetch", ref.provider_content_id, "0", 1)
        if not page.items:
            raise ContentIntegrationError(
                IntegrationErrorCode.INVALID_CONTENT_REF, "content not found"
            )
        return self.native_from_model(page.items[0])

    async def creator(
        self, query: CreatorQuery, access: AccessHandle
    ) -> ContentPage[ContentPreview]:
        self._access(access)
        return await self._page("creator", query.creator_id, query.page)

    def native_from_payload(self, payload: Mapping[str, object]) -> NativeContent:
        return self.native_from_model(V2EXTopic.model_validate(payload))

    def native_from_model(self, item: V2EXTopic) -> NativeContent:
        return NativeContent(
            ref=ContentRef(
                provider_id=V2EX_ID,
                content_kind=TOPIC_KIND,
                provider_content_id=str(item.id),
                canonical_url=f"https://www.v2ex.com/t/{item.id}",
            ),
            schema_version=1,
            payload=item,
        )

    def preview(self, content: NativeContent) -> ContentPreview:
        return projections.preview(content)

    def recommendation_candidate(self, content: NativeContent) -> RecommendationCandidate:
        return projections.recommendation_candidate(content)

    def search_document(self, content: NativeContent) -> SearchDocument:
        return projections.search_document(content)

    def card_data(self, content: NativeContent) -> CardData:
        return projections.card_data(content)

    async def _page(
        self, operation: str, argument: str, page: PageRequest
    ) -> ContentPage[ContentPreview]:
        cursor = self._cursor(page)
        result = await self._client.page(
            operation, argument, cursor, min(page.limit, self._MAX_ITEMS)
        )
        return ContentPage(
            items=tuple(
                self.preview(self.native_from_model(item)) for item in result.items[: page.limit]
            ),
            next_cursor=ProviderCursor(provider_id=V2EX_ID, value=result.next_cursor)
            if result.next_cursor
            else None,
        )

    @staticmethod
    def _access(access: AccessHandle) -> None:
        if access.provider_id != "v2ex" or Permission.READ_PUBLIC not in access.permissions:
            raise ContentIntegrationError(
                IntegrationErrorCode.ACCESS_DENIED, "public read permission required"
            )

    @staticmethod
    def _cursor(page: PageRequest) -> str:
        if page.cursor is None:
            return "0"
        if page.cursor.provider_id != V2EX_ID:
            raise ContentIntegrationError(
                IntegrationErrorCode.INVALID_CONTENT_REF, "cursor belongs to another provider"
            )
        return page.cursor.value

    @staticmethod
    def _ref(ref: ContentRef) -> None:
        if ref.provider_id != V2EX_ID or ref.content_kind != TOPIC_KIND:
            raise ContentIntegrationError(
                IntegrationErrorCode.INVALID_CONTENT_REF, "content belongs to another provider"
            )
