"""Read-only Bilibili connector around retained API/extension transports."""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

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
    text,
    timestamp,
)


class BilibiliTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class BilibiliSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = True


_MANIFEST = SourceManifest(
    source_id="bilibili",
    display_name="Bilibili",
    capabilities=frozenset(
        {
            SourceCapability.ACTIVITY_IMPORT,
            SourceCapability.SEARCH,
            SourceCapability.TRENDING,
            SourceCapability.RELATED,
            SourceCapability.EXPLORE,
        }
    ),
    requires_account=True,
)


class BilibiliConnector(NormalizingConnector):
    def __init__(
        self, transport: BilibiliTransport, settings: BilibiliSettings | None = None
    ) -> None:
        super().__init__(
            manifest=_MANIFEST,
            transport=transport,
            settings=settings or BilibiliSettings(),
            normalize_content=_content,
            normalize_activity=_activity,
        )


def _content(row: dict[str, Any]) -> ContentItem | None:
    external_id = first_text(row.get("bvid"), row.get("content_id"), row.get("id"))
    if not external_id:
        return None
    return content_item(
        source_id="bilibili",
        external_id=external_id,
        url=first_text(row.get("url"), row.get("content_url"))
        or f"https://www.bilibili.com/video/{external_id}",
        title=first_text(row.get("title"), row.get("name")) or external_id,
        summary=first_text(row.get("description"), row.get("desc")),
        creator=first_text(nested(row, "owner", "name"), row.get("up_name")) or None,
        published_at=timestamp(row.get("pubdate") or row.get("published_at")),
        media_type="video",
        metadata={"duration": text(row.get("duration"))} if row.get("duration") else {},
    )


def _activity(row: dict[str, Any]) -> ActivityEvent | None:
    external_id = first_text(row.get("bvid"), row.get("content_id"), row.get("id"))
    if not external_id:
        return None
    return activity_event(
        source_id="bilibili",
        kind=activity_kind(row.get("event_type") or row.get("kind")),
        external_id=external_id,
        occurred_at=timestamp(row.get("occurred_at") or row.get("view_at")),
        url=first_text(row.get("url")) or f"https://www.bilibili.com/video/{external_id}",
        title=first_text(row.get("title")) or None,
    )
