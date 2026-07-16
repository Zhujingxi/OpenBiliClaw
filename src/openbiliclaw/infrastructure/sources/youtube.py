"""Read-only YouTube connector around scraper, Takeout, or browser transports."""

from __future__ import annotations

from typing import Any, Protocol

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
)
from openbiliclaw.infrastructure.sources._base import (
    NormalizingConnector,
    RoutedTransport,
    activity_event,
    activity_kind,
    content_item,
    first_text,
    operation_spec,
    timestamp,
)
from openbiliclaw.infrastructure.sources.browser_tasks import QueuedBrowserTransport


class YouTubeTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class YouTubeReadClient(Protocol):
    async def search_videos(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]: ...
    async def get_trending(self, *, limit: int = 50) -> list[dict[str, Any]]: ...
    async def get_channel_videos(
        self, channel_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]: ...


class YouTubeDirectTransport:
    def __init__(self, client: YouTubeReadClient) -> None:
        self._client = client

    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == SourceOperation.SEARCH:
            return await self._client.search_videos(query or "", limit=limit)
        if operation == SourceOperation.TRENDING:
            return await self._client.get_trending(limit=limit)
        if operation == SourceOperation.CREATOR:
            return await self._client.get_channel_videos(query or "", limit=limit)
        raise ValueError(f"unsupported YouTube operation: {operation}")


class YouTubeSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = False
    daily_search_budget: int = Field(default=0, ge=0)
    daily_trending_budget: int = Field(default=0, ge=0)
    daily_channel_budget: int = Field(default=0, ge=0)
    request_interval_seconds: int = Field(default=2, ge=1)
    min_interval_minutes: int = Field(default=60, ge=1)


_MANIFEST = SourceManifest(
    source_id=SourceId.YOUTUBE,
    display_name="YouTube",
    capabilities=frozenset(
        {
            SourceCapability.AUTHENTICATION,
            SourceCapability.BOOTSTRAP_IMPORT,
            SourceCapability.ACTIVITY_COLLECTION,
            SourceCapability.SEARCH,
            SourceCapability.TRENDING_FEED,
            SourceCapability.CREATOR_DISCOVERY,
            SourceCapability.BROWSER_ASSISTED,
        }
    ),
    operations=(
        operation_spec(
            SourceOperation.BOOTSTRAP_IMPORT,
            SourceCapability.BOOTSTRAP_IMPORT,
            result_kind=SourceResultKind.ACTIVITY,
            requires_auth=True,
            transport_kind=SourceTransportKind.BROWSER,
        ),
        operation_spec(
            SourceOperation.SEARCH,
            SourceCapability.SEARCH,
            requires_auth=False,
            transport_kind=SourceTransportKind.DIRECT,
        ),
        operation_spec(
            SourceOperation.TRENDING,
            SourceCapability.TRENDING_FEED,
            requires_auth=False,
            transport_kind=SourceTransportKind.DIRECT,
        ),
        operation_spec(
            SourceOperation.CREATOR,
            SourceCapability.CREATOR_DISCOVERY,
            requires_auth=False,
            transport_kind=SourceTransportKind.DIRECT,
        ),
    ),
)


class YouTubeConnector(NormalizingConnector):
    def __init__(
        self, transport: YouTubeTransport, settings: YouTubeSettings | None = None
    ) -> None:
        super().__init__(
            manifest=_MANIFEST,
            transport=transport,
            settings=settings or YouTubeSettings(),
            normalize_content=_content,
            normalize_activity=_activity,
        )


def build_youtube_connector(
    client: YouTubeReadClient,
    task_service: object,
    settings: YouTubeSettings | None = None,
) -> YouTubeConnector:
    direct = YouTubeDirectTransport(client)
    browser = QueuedBrowserTransport(task_service, SourceId.YOUTUBE)  # type: ignore[arg-type]
    return YouTubeConnector(
        RoutedTransport(
            {
                SourceOperation.BOOTSTRAP_IMPORT.value: browser,
                SourceOperation.SEARCH.value: direct,
                SourceOperation.TRENDING.value: direct,
                SourceOperation.CREATOR.value: direct,
            }
        ),
        settings,
    )


def _external_id(row: dict[str, Any]) -> str:
    return first_text(row.get("videoId"), row.get("video_id"), row.get("id"))


def _content(row: dict[str, Any]) -> ContentItem | None:
    external_id = _external_id(row)
    if not external_id:
        return None
    return content_item(
        source_id="youtube",
        external_id=external_id,
        url=first_text(row.get("url"), row.get("webpage_url"))
        or f"https://www.youtube.com/watch?v={external_id}",
        title=first_text(row.get("title")) or external_id,
        summary=first_text(row.get("description")),
        creator=first_text(row.get("ownerText"), row.get("channel"), row.get("uploader")) or None,
        published_at=timestamp(row.get("published_at") or row.get("timestamp")),
        media_type="video",
    )


def _activity(row: dict[str, Any]) -> ActivityEvent | None:
    external_id = _external_id(row) or first_text(row.get("channel_id"), row.get("url"))
    if not external_id:
        return None
    return activity_event(
        source_id="youtube",
        kind=activity_kind(row.get("scope") or row.get("event_type")),
        external_id=external_id,
        occurred_at=timestamp(row.get("occurred_at") or row.get("time")),
        url=first_text(row.get("url"))
        or (f"https://www.youtube.com/watch?v={external_id}" if _external_id(row) else None),
        title=first_text(row.get("title")) or None,
        metadata={"scope": first_text(row.get("scope"))},
    )
