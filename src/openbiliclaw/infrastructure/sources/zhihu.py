"""Read-only Zhihu connector around logged-in extension tasks."""

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
    nested,
    timestamp,
)


class ZhihuTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class ZhihuSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = False
    source_modes: tuple[str, ...] = ("search", "hot", "feed", "creator", "related")
    daily_search_budget: int = Field(default=0, ge=0)
    daily_hot_budget: int = Field(default=0, ge=0)
    daily_feed_budget: int = Field(default=0, ge=0)
    daily_creator_budget: int = Field(default=0, ge=0)
    daily_related_budget: int = Field(default=0, ge=0)
    request_interval_seconds: int = Field(default=3, ge=1)
    min_interval_minutes: int = Field(default=60, ge=1)


_MANIFEST = SourceManifest(
    source_id="zhihu",
    display_name="Zhihu",
    capabilities=frozenset(
        {
            SourceCapability.ACTIVITY_IMPORT,
            SourceCapability.SEARCH,
            SourceCapability.TRENDING,
            SourceCapability.RECOMMENDED,
            SourceCapability.CREATOR,
            SourceCapability.RELATED,
        }
    ),
    requires_account=True,
)


class ZhihuConnector(NormalizingConnector):
    def __init__(self, transport: ZhihuTransport, settings: ZhihuSettings | None = None) -> None:
        super().__init__(
            manifest=_MANIFEST,
            transport=transport,
            settings=settings or ZhihuSettings(),
            normalize_content=_content,
            normalize_activity=_activity,
        )


def _external_id(row: dict[str, Any]) -> str:
    return first_text(row.get("id"), row.get("content_id"), row.get("answer_id"))


def _url(row: dict[str, Any], external_id: str) -> str:
    explicit = first_text(row.get("url"), row.get("content_url"))
    if explicit:
        return explicit
    question_id = first_text(row.get("question_id"), nested(row, "question", "id"))
    content_type = first_text(row.get("type"), row.get("content_type"))
    if content_type == "answer" and question_id:
        return f"https://www.zhihu.com/question/{question_id}/answer/{external_id}"
    return f"https://www.zhihu.com/{content_type or 'question'}/{external_id}"


def _content(row: dict[str, Any]) -> ContentItem | None:
    external_id = _external_id(row)
    if not external_id:
        return None
    media_type = first_text(row.get("type"), row.get("content_type")) or "answer"
    return content_item(
        source_id="zhihu",
        external_id=external_id,
        url=_url(row, external_id),
        title=first_text(row.get("title"), nested(row, "question", "title")) or external_id,
        summary=first_text(row.get("excerpt"), row.get("content"), row.get("description")),
        creator=first_text(nested(row, "author", "name"), row.get("author_name")) or None,
        published_at=timestamp(row.get("created_time") or row.get("published_at")),
        media_type=media_type,
    )


def _activity(row: dict[str, Any]) -> ActivityEvent | None:
    external_id = _external_id(row)
    if not external_id:
        return None
    return activity_event(
        source_id="zhihu",
        kind=activity_kind(row.get("scope") or row.get("event_type")),
        external_id=external_id,
        occurred_at=timestamp(row.get("occurred_at") or row.get("created_time")),
        url=_url(row, external_id),
        title=first_text(row.get("title"), nested(row, "question", "title")) or None,
        metadata={"scope": first_text(row.get("scope"))},
    )
