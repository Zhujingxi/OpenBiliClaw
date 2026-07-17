"""Read-only X/Twitter connector around the retained twitter-cli transport."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.features.activity.domain import ActivityEvent  # noqa: TC001
from openbiliclaw.features.feed.domain import ContentItem  # noqa: TC001
from openbiliclaw.features.sources.domain import (
    SourceCapability,
    SourceId,
    SourceManifest,
    SourceOperation,
    SourceResultKind,
    SourceTransportKind,
    source_form_schema_fields,
)
from openbiliclaw.infrastructure.sources._base import (
    NormalizingConnector,
    content_item,
    first_text,
    nested,
    operation_spec,
    timestamp,
)


class TwitterTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class TwitterReadClient(Protocol):
    async def search(
        self, query: str, *, limit: int, product: str = "Top"
    ) -> list[dict[str, Any]]: ...
    async def for_you(self, *, limit: int) -> list[dict[str, Any]]: ...
    async def user_tweets(self, handle: str, *, limit: int) -> list[dict[str, Any]]: ...
    async def likes(self, *, limit: int) -> list[dict[str, Any]]: ...
    async def bookmarks(self, *, limit: int) -> list[dict[str, Any]]: ...


class TwitterCliTransport:
    def __init__(self, client: TwitterReadClient) -> None:
        self._client = client

    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == SourceOperation.BOOTSTRAP_IMPORT:
            liked = await self._client.likes(limit=limit)
            bookmarked = await self._client.bookmarks(limit=limit)
            return [dict(row, scope="liked") for row in liked] + [
                dict(row, scope="saved") for row in bookmarked
            ]
        if operation == SourceOperation.SEARCH:
            return await self._client.search(query or "", limit=limit)
        if operation == SourceOperation.FEED:
            return await self._client.for_you(limit=limit)
        if operation == SourceOperation.CREATOR:
            return await self._client.user_tweets(query or "", limit=limit)
        raise ValueError(f"unsupported X operation: {operation}")


def build_twitter_connector(
    client: TwitterReadClient, settings: TwitterSettings | None = None
) -> TwitterConnector:
    return TwitterConnector(TwitterCliTransport(client), settings)


class TwitterSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = False
    mode: Literal["cookie"] = "cookie"
    daily_search_budget: int = Field(default=0, ge=0)
    daily_feed_budget: int = Field(default=0, ge=0)
    daily_creator_budget: int = Field(default=0, ge=0)
    request_interval_seconds: int = Field(default=3, ge=1)
    min_interval_minutes: int = Field(default=60, ge=1)


_MANIFEST = SourceManifest(
    source_id=SourceId.TWITTER,
    display_name="X (Twitter)",
    **source_form_schema_fields(TwitterSettings),
    capabilities=frozenset(
        {
            SourceCapability.AUTHENTICATION,
            SourceCapability.BOOTSTRAP_IMPORT,
            SourceCapability.ACTIVITY_COLLECTION,
            SourceCapability.SEARCH,
            SourceCapability.TRENDING_FEED,
            SourceCapability.CREATOR_DISCOVERY,
        }
    ),
    operations=(
        operation_spec(
            SourceOperation.BOOTSTRAP_IMPORT,
            SourceCapability.BOOTSTRAP_IMPORT,
            result_kind=SourceResultKind.ACTIVITY,
            requires_auth=True,
            transport_kind=SourceTransportKind.CLI,
        ),
        operation_spec(
            SourceOperation.SEARCH,
            SourceCapability.SEARCH,
            requires_auth=True,
            transport_kind=SourceTransportKind.CLI,
        ),
        operation_spec(
            SourceOperation.FEED,
            SourceCapability.TRENDING_FEED,
            requires_auth=True,
            transport_kind=SourceTransportKind.CLI,
        ),
        operation_spec(
            SourceOperation.CREATOR,
            SourceCapability.CREATOR_DISCOVERY,
            requires_auth=True,
            transport_kind=SourceTransportKind.CLI,
        ),
    ),
)


class TwitterConnector(NormalizingConnector):
    def __init__(
        self, transport: TwitterTransport, settings: TwitterSettings | None = None
    ) -> None:
        super().__init__(
            manifest=_MANIFEST,
            transport=transport,
            settings=settings or TwitterSettings(),
            normalize_content=_content,
            normalize_activity=_activity,
        )


def _activity(row: dict[str, Any]) -> ActivityEvent | None:
    from openbiliclaw.infrastructure.sources._base import activity_event, activity_kind

    external_id = first_text(row.get("rest_id"), row.get("id_str"), row.get("id"))
    if not external_id:
        return None
    return activity_event(
        source_id="twitter",
        kind=activity_kind(row.get("scope") or row.get("event_type")),
        external_id=external_id,
        occurred_at=timestamp(row.get("createdAtISO") or row.get("created_at")),
        url=first_text(row.get("url")),
        title=first_text(row.get("full_text"), row.get("text")) or None,
    )


def _content(row: dict[str, Any]) -> ContentItem | None:
    external_id = first_text(row.get("rest_id"), row.get("id_str"), row.get("id"))
    if not external_id:
        return None
    creator = first_text(
        nested(row, "user", "screen_name"),
        nested(row, "author", "username"),
        nested(row, "author", "screenName"),
        row.get("username"),
    )
    body = first_text(row.get("full_text"), row.get("text"), row.get("body"))
    return content_item(
        source_id="twitter",
        external_id=external_id,
        url=first_text(row.get("url")) or f"https://x.com/{creator or 'i'}/status/{external_id}",
        title=body[:200] or external_id,
        summary=body,
        creator=creator or None,
        published_at=timestamp(
            row.get("createdAtISO") or row.get("created_at") or row.get("published_at")
        ),
        media_type="tweet",
    )
