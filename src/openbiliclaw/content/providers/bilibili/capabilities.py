"""Complete typed Bilibili capability implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import ConfigDict

from openbiliclaw.access.models import (
    AccessHandle,
    CredentialAccessHandle,
    Permission,
)
from openbiliclaw.content.integration.actions import ActionRequest, ActionResult
from openbiliclaw.content.integration.capabilities import (
    ContentPage,
    CreatorQuery,
    FeedQuery,
    PageRequest,
    ProviderCursor,
    ProviderObservation,
    SearchQuery,
)
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ContentKind, ContentRef
from openbiliclaw.content.integration.native import NativeContent
from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    RecommendationCandidate,
    SearchDocument,
)

from . import projections
from .manifest import ARTICLE_KIND, BILIBILI_ID, VIDEO_KIND
from .models import ITEM_ADAPTER, BilibiliArticle, BilibiliItem, BilibiliVideo

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .client import BilibiliClient


class BilibiliActionRequest(ActionRequest):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BilibiliProvider:
    """Reference first-party provider; all result data is typed at the client boundary."""

    _MAX_ITEMS = 50

    def __init__(self, client: BilibiliClient) -> None:
        self._client = client
        self._action_results: dict[str, ActionResult] = {}

    def __repr__(self) -> str:
        return "BilibiliProvider(client=typed, credentials=<opaque>)"

    async def search(self, query: SearchQuery, access: AccessHandle) -> ContentPage[ContentPreview]:
        self._public_access(access)
        cursor = self._cursor(query.page)
        page = await self._client.page(
            "/x/web-interface/search/type",
            {"keyword": query.text, "limit": self._limit(query.page), "cursor": cursor},
        )
        return self._preview_page(page.items, page.next_cursor, query.page.limit)

    async def feed(self, query: FeedQuery, access: AccessHandle) -> ContentPage[ContentPreview]:
        self._public_access(access)
        if query.feed_id not in {None, "popular"}:
            # Rendered personalized homepage depends on browser-session execution,
            # intentionally absent from target architecture.
            raise ContentIntegrationError(
                IntegrationErrorCode.UNAVAILABLE_CAPABILITY,
                "requested feed mode is unavailable",
            )
        page = await self._client.page(
            "/x/web-interface/popular",
            {"limit": self._limit(query.page), "cursor": self._cursor(query.page)},
        )
        return self._preview_page(page.items, page.next_cursor, query.page.limit)

    async def fetch(self, ref: ContentRef, access: AccessHandle) -> NativeContent:
        self._public_access(access)
        self._ref(ref)
        path = "/x/web-interface/view" if ref.content_kind == VIDEO_KIND else "/x/article/viewinfo"
        item = await self._client.item(path, {"id": ref.provider_content_id})
        return self.native_from_model(item)

    async def related(
        self, ref: ContentRef, page: PageRequest, access: AccessHandle
    ) -> ContentPage[ContentPreview]:
        self._public_access(access)
        self._ref(ref, expected=VIDEO_KIND)
        result = await self._client.page(
            "/x/web-interface/archive/related",
            {
                "bvid": ref.provider_content_id,
                "limit": self._limit(page),
                "cursor": self._cursor(page),
            },
        )
        return self._preview_page(result.items, result.next_cursor, page.limit)

    async def creator(
        self, query: CreatorQuery, access: AccessHandle
    ) -> ContentPage[ContentPreview]:
        self._public_access(access)
        result = await self._client.page(
            "/x/space/wbi/arc/search",
            {
                "mid": query.creator_id,
                "limit": self._limit(query.page),
                "cursor": self._cursor(query.page),
            },
        )
        return self._preview_page(result.items, result.next_cursor, query.page.limit)

    async def history(self, page: PageRequest, access: AccessHandle) -> ContentPage[ContentPreview]:
        credential = self._private_access(access, Permission.READ_PRIVATE)
        result = await self._client.page(
            "/x/web-interface/history/cursor",
            {"limit": self._limit(page), "cursor": self._cursor(page)},
            credential,
        )
        return self._preview_page(result.items, result.next_cursor, page.limit)

    async def saved(self, page: PageRequest, access: AccessHandle) -> ContentPage[ContentPreview]:
        credential = self._private_access(access, Permission.READ_PRIVATE)
        result = await self._client.page(
            "/x/v3/fav/resource/list",
            {"limit": self._limit(page), "cursor": self._cursor(page)},
            credential,
        )
        return self._preview_page(result.items, result.next_cursor, page.limit)

    async def execute_action(
        self, request: BilibiliActionRequest, access: AccessHandle
    ) -> ActionResult:
        credential = self._private_access(access, Permission.WRITE)
        self._ref(request.ref, expected=VIDEO_KIND)
        if request.action_id != "save":
            raise ContentIntegrationError(
                IntegrationErrorCode.UNAVAILABLE_CAPABILITY, "action is not implemented"
            )
        if request.confirmation.expires_at <= datetime.now(UTC):
            raise ContentIntegrationError(
                IntegrationErrorCode.ACCESS_DENIED, "confirmation expired"
            )
        cached = self._action_results.get(request.idempotency_key)
        if cached is not None:
            if cached.ref != request.ref:
                raise ContentIntegrationError(
                    IntegrationErrorCode.ACCESS_DENIED, "idempotency key scope mismatch"
                )
            return cached
        response = await self._client.action(
            "/x/v3/fav/resource/deal",
            {"rid": request.ref.provider_content_id, "type": 2, "add_media_ids": "default"},
            credential,
            idempotency_key=request.idempotency_key,
        )
        if not response.accepted:
            raise ContentIntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider rejected action"
            )
        result = ActionResult(
            action_id=request.action_id,
            ref=request.ref,
            idempotency_key=request.idempotency_key,
            completed_at=datetime.now(UTC),
        )
        self._action_results[request.idempotency_key] = result
        return result

    def native_from_payload(self, payload: Mapping[str, object]) -> NativeContent:
        return self.native_from_model(ITEM_ADAPTER.validate_python(payload))

    def native_from_model(self, item: BilibiliItem) -> NativeContent:
        kind = VIDEO_KIND if isinstance(item, BilibiliVideo) else ARTICLE_KIND
        canonical = (
            f"https://www.bilibili.com/video/{item.id}"
            if isinstance(item, BilibiliVideo)
            else f"https://www.bilibili.com/read/{item.id}"
        )
        return NativeContent(
            ref=ContentRef(
                provider_id=BILIBILI_ID,
                content_kind=kind,
                provider_content_id=item.id,
                canonical_url=canonical,
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

    def observations(self, content: NativeContent) -> tuple[ProviderObservation, ...]:
        payload = content.payload
        if not isinstance(payload, BilibiliVideo | BilibiliArticle):
            raise ValueError("Bilibili observation requires Bilibili payload")
        return (
            ProviderObservation(
                ref=content.ref,
                event_type="content_seen",
                occurred_at=datetime.fromtimestamp(payload.published_at, tz=UTC),
            ),
        )

    def _preview_page(
        self, items: tuple[BilibiliItem, ...], cursor: str | None, requested_limit: int
    ) -> ContentPage[ContentPreview]:
        bounded = items[: min(requested_limit, self._MAX_ITEMS)]
        return ContentPage[ContentPreview](
            items=tuple(self.preview(self.native_from_model(item)) for item in bounded),
            next_cursor=(
                ProviderCursor(provider_id=BILIBILI_ID, value=cursor)
                if cursor is not None
                else None
            ),
        )

    @classmethod
    def _limit(cls, page: PageRequest) -> int:
        return min(page.limit, cls._MAX_ITEMS)

    @staticmethod
    def _cursor(page: PageRequest) -> str:
        if page.cursor is None:
            return "0"
        if page.cursor.provider_id != BILIBILI_ID:
            raise ContentIntegrationError(
                IntegrationErrorCode.INVALID_CONTENT_REF, "cursor belongs to another provider"
            )
        return page.cursor.value

    @staticmethod
    def _ref(ref: ContentRef, *, expected: ContentKind | None = None) -> None:
        if ref.provider_id != BILIBILI_ID or ref.content_kind not in {VIDEO_KIND, ARTICLE_KIND}:
            raise ContentIntegrationError(
                IntegrationErrorCode.INVALID_CONTENT_REF, "content belongs to another provider"
            )
        if expected is not None and ref.content_kind != expected:
            raise ContentIntegrationError(
                IntegrationErrorCode.INVALID_CONTENT_REF, "content kind is unsupported"
            )

    @staticmethod
    def _public_access(access: AccessHandle) -> None:
        if access.provider_id != "bilibili" or Permission.READ_PUBLIC not in access.permissions:
            raise ContentIntegrationError(
                IntegrationErrorCode.ACCESS_DENIED, "public read permission required"
            )

    @staticmethod
    def _private_access(access: AccessHandle, permission: Permission) -> CredentialAccessHandle:
        if (
            not isinstance(access, CredentialAccessHandle)
            or access.provider_id != "bilibili"
            or permission not in access.permissions
        ):
            raise ContentIntegrationError(
                IntegrationErrorCode.ACCESS_DENIED, "credential scope is insufficient"
            )
        return access
