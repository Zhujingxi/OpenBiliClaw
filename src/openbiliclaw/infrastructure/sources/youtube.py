"""Read-only YouTube connector around scraper, Takeout, or browser transports."""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.features.activity.domain import ActivityEvent
from openbiliclaw.features.feed.domain import ContentItem
from openbiliclaw.features.sources.domain import SourceCapability, SourceManifest
from openbiliclaw.infrastructure.sources._base import (
    NormalizingConnector,
    activity_event,
    activity_kind,
    content_item,
    first_text,
    timestamp,
)


class YouTubeTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class YouTubeSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = False
    daily_search_budget: int = Field(default=0, ge=0)
    daily_trending_budget: int = Field(default=0, ge=0)
    daily_channel_budget: int = Field(default=0, ge=0)
    request_interval_seconds: int = Field(default=2, ge=1)
    min_interval_minutes: int = Field(default=60, ge=1)


_MANIFEST = SourceManifest(
    source_id="youtube",
    display_name="YouTube",
    capabilities=frozenset(
        {
            SourceCapability.ACTIVITY_IMPORT,
            SourceCapability.SEARCH,
            SourceCapability.TRENDING,
            SourceCapability.CREATOR,
        }
    ),
    requires_account=False,
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
