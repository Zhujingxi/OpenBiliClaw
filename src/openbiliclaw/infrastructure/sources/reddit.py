"""Read-only Reddit connector around retained rdt-cli or extension transports."""

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
    timestamp,
)


class RedditTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class RedditSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = False
    backend: Literal["rdt", "extension"] = "rdt"
    source_modes: tuple[str, ...] = ("search", "hot", "subreddit", "related")
    daily_search_budget: int = Field(default=300, ge=0)
    daily_hot_budget: int = Field(default=300, ge=0)
    daily_subreddit_budget: int = Field(default=300, ge=0)
    daily_related_budget: int = Field(default=300, ge=0)
    request_interval_seconds: int = Field(default=3, ge=1)
    min_interval_minutes: int = Field(default=60, ge=1)


_MANIFEST = SourceManifest(
    source_id="reddit",
    display_name="Reddit",
    capabilities=frozenset(
        {
            SourceCapability.ACTIVITY_IMPORT,
            SourceCapability.SEARCH,
            SourceCapability.TRENDING,
            SourceCapability.COMMUNITY,
            SourceCapability.RELATED,
        }
    ),
    requires_account=True,
)


class RedditConnector(NormalizingConnector):
    def __init__(self, transport: RedditTransport, settings: RedditSettings | None = None) -> None:
        super().__init__(
            manifest=_MANIFEST,
            transport=transport,
            settings=settings or RedditSettings(),
            normalize_content=_content,
            normalize_activity=_activity,
        )


def _external_id(row: dict[str, Any]) -> str:
    return first_text(row.get("name"), row.get("id"), row.get("content_id"))


def _url(row: dict[str, Any], external_id: str) -> str:
    explicit = first_text(row.get("url"), row.get("permalink"))
    if explicit.startswith("/"):
        return f"https://www.reddit.com{explicit}"
    return explicit or f"https://www.reddit.com/comments/{external_id}/"


def _content(row: dict[str, Any]) -> ContentItem | None:
    external_id = _external_id(row)
    if not external_id:
        return None
    body = first_text(row.get("selftext"), row.get("body"), row.get("text"))
    return content_item(
        source_id="reddit",
        external_id=external_id,
        url=_url(row, external_id),
        title=first_text(row.get("title"), row.get("name"), body[:200]) or external_id,
        summary=body,
        creator=first_text(row.get("author")) or None,
        published_at=timestamp(row.get("created_utc") or row.get("published_at")),
        media_type="comment" if external_id.startswith("t1_") else "post",
        metadata={"subreddit": first_text(row.get("subreddit"))},
    )


def _activity(row: dict[str, Any]) -> ActivityEvent | None:
    external_id = _external_id(row)
    if not external_id:
        return None
    return activity_event(
        source_id="reddit",
        kind=activity_kind(row.get("scope") or row.get("event_type")),
        external_id=external_id,
        occurred_at=timestamp(row.get("occurred_at") or row.get("created_utc")),
        url=_url(row, external_id),
        title=first_text(row.get("title"), row.get("body")) or None,
        metadata={"scope": first_text(row.get("scope"))},
    )
