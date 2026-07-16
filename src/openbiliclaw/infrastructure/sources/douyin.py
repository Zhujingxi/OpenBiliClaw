"""Read-only Douyin connector around direct HTTP or logged-in browser transports."""

from typing import Any, Literal, Protocol

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
    nested,
    timestamp,
)


class DouyinTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class DouyinSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = False
    mode: Literal["direct", "extension"] = "direct"
    daily_search_budget: int = Field(default=0, ge=0)
    daily_hot_budget: int = Field(default=0, ge=0)
    daily_feed_budget: int = Field(default=0, ge=0)
    request_interval_seconds: int = Field(default=2, ge=1)


_MANIFEST = SourceManifest(
    source_id="douyin",
    display_name="Douyin",
    capabilities=frozenset(
        {
            SourceCapability.ACTIVITY_IMPORT,
            SourceCapability.SEARCH,
            SourceCapability.TRENDING,
            SourceCapability.RECOMMENDED,
        }
    ),
    requires_account=True,
)


class DouyinConnector(NormalizingConnector):
    def __init__(self, transport: DouyinTransport, settings: DouyinSettings | None = None) -> None:
        super().__init__(
            manifest=_MANIFEST,
            transport=transport,
            settings=settings or DouyinSettings(),
            normalize_content=_content,
            normalize_activity=_activity,
        )


def _content(row: dict[str, Any]) -> ContentItem | None:
    external_id = first_text(row.get("aweme_id"), row.get("id"), row.get("content_id"))
    if not external_id:
        return None
    return content_item(
        source_id="douyin",
        external_id=external_id,
        url=first_text(row.get("url"), row.get("share_url"))
        or f"https://www.douyin.com/video/{external_id}",
        title=first_text(row.get("desc"), nested(row, "share_info", "share_title")) or external_id,
        summary=first_text(row.get("desc")),
        creator=first_text(nested(row, "author", "nickname"), row.get("nickname")) or None,
        published_at=timestamp(row.get("create_time") or row.get("published_at")),
        media_type="video",
    )


def _activity(row: dict[str, Any]) -> ActivityEvent | None:
    external_id = first_text(
        row.get("aweme_id"), row.get("creator_sec_uid"), row.get("id"), row.get("content_id")
    )
    if not external_id:
        return None
    kind = activity_kind(row.get("scope") or row.get("event_type"))
    return activity_event(
        source_id="douyin",
        kind=kind,
        external_id=external_id,
        occurred_at=timestamp(row.get("occurred_at") or row.get("create_time")),
        url=(first_text(row.get("url")) or f"https://www.douyin.com/video/{external_id}")
        if kind is not None
        else None,
        title=first_text(row.get("desc"), row.get("nickname")) or None,
        metadata={"scope": first_text(row.get("scope"))},
    )
