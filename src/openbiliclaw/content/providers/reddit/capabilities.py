"""Reddit read capabilities and purpose-specific projections."""

from datetime import UTC, datetime

from openbiliclaw.access.models import AccessHandle, CredentialAccessHandle, Permission
from openbiliclaw.content.integration.capabilities import ContentPage, ProviderCursor, SearchQuery
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ContentRef
from openbiliclaw.content.integration.native import NativeContent
from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    ProjectionProvenance,
    RecommendationCandidate,
    SearchDocument,
)

from .client import RedditClient
from .manifest import POST_KIND, REDDIT_ID
from .models import RedditItem


class RedditProvider:
    _MAX_ITEMS = 50

    def __init__(self, client: RedditClient) -> None:
        self._client = client

    async def search(self, query: SearchQuery, access: AccessHandle) -> ContentPage[ContentPreview]:
        credential = self._access(access)
        cursor = self._cursor(query)
        page = await self._client.search(
            query.text, cursor, min(query.page.limit, self._MAX_ITEMS), credential
        )
        items = page.items[: query.page.limit]
        return ContentPage[ContentPreview](
            items=tuple(self.preview(self.native(item)) for item in items),
            next_cursor=ProviderCursor(provider_id=REDDIT_ID, value=page.next_cursor)
            if page.next_cursor
            else None,
        )

    async def fetch(self, ref: ContentRef, access: AccessHandle) -> NativeContent:
        credential = self._access(access)
        self._ref(ref)
        return self.native(await self._client.fetch(ref.provider_content_id, credential))

    def native_from_bytes(self, raw: bytes) -> NativeContent:
        return self.native(RedditItem.model_validate_json(raw))

    @staticmethod
    def native(item: RedditItem) -> NativeContent:
        return NativeContent(
            ref=ContentRef(
                provider_id=REDDIT_ID,
                content_kind=POST_KIND,
                provider_content_id=item.id,
                canonical_url=f"https://www.reddit.com/comments/{item.id}",
            ),
            schema_version=1,
            payload=item,
        )

    @staticmethod
    def preview(content: NativeContent) -> ContentPreview:
        item = RedditProvider._payload(content)
        provenance = RedditProvider._provenance(content)
        return ContentPreview(
            ref=content.ref,
            title=item.title,
            summary=item.body,
            creator_label=item.author or None,
            source_timestamp=datetime.fromtimestamp(item.published_at, tz=UTC),
            provenance=provenance,
        )

    @staticmethod
    def card_data(content: NativeContent) -> CardData:
        item = RedditProvider._payload(content)
        timestamp = datetime.fromtimestamp(item.published_at, tz=UTC)
        return CardData(
            ref=content.ref,
            title=item.title,
            summary=item.body,
            badge="Reddit",
            image_url=None,
            source_timestamp=timestamp,
            provenance=RedditProvider._provenance(content),
        )

    def recommendation_candidate(self, content: NativeContent) -> RecommendationCandidate:
        item = self._payload(content)
        timestamp = datetime.fromtimestamp(item.published_at, tz=UTC)
        return RecommendationCandidate(
            ref=content.ref,
            title=item.title,
            summary=item.body,
            discovery_reason="reddit:search",
            source_timestamp=timestamp,
            provenance=self._provenance(content),
        )

    def search_document(self, content: NativeContent) -> SearchDocument:
        item = self._payload(content)
        timestamp = datetime.fromtimestamp(item.published_at, tz=UTC)
        return SearchDocument(
            ref=content.ref,
            title=item.title,
            body=item.body or item.title,
            source_timestamp=timestamp,
            provenance=self._provenance(content),
        )

    @staticmethod
    def _payload(content: NativeContent) -> RedditItem:
        if not isinstance(content.payload, RedditItem):
            raise ValueError("Reddit payload required")
        return content.payload

    @staticmethod
    def _provenance(content: NativeContent) -> ProjectionProvenance:
        item = RedditProvider._payload(content)
        timestamp = datetime.fromtimestamp(item.published_at, tz=UTC)
        return ProjectionProvenance(
            ref=content.ref, native_schema_version=content.schema_version, projected_at=timestamp
        )

    @staticmethod
    def _cursor(query: SearchQuery) -> str | None:
        cursor = query.page.cursor
        if cursor is not None and cursor.provider_id != REDDIT_ID:
            raise ContentIntegrationError(
                IntegrationErrorCode.INVALID_CONTENT_REF, "cursor belongs to another provider"
            )
        return cursor.value if cursor else None

    @staticmethod
    def _ref(ref: ContentRef) -> None:
        if ref.provider_id != REDDIT_ID or ref.content_kind != POST_KIND:
            raise ContentIntegrationError(
                IntegrationErrorCode.INVALID_CONTENT_REF, "content belongs to another provider"
            )

    @staticmethod
    def _access(access: AccessHandle) -> CredentialAccessHandle:
        if (
            not isinstance(access, CredentialAccessHandle)
            or access.provider_id != "reddit"
            or Permission.READ_PRIVATE not in access.permissions
        ):
            raise ContentIntegrationError(
                IntegrationErrorCode.ACCESS_DENIED, "private read permission required"
            )
        return access
