"""Read-only Zhihu connector around logged-in extension tasks."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

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
    activity_event,
    activity_kind,
    content_item,
    first_text,
    nested,
    operation_spec,
    timestamp,
)
from openbiliclaw.infrastructure.sources.browser_tasks import QueuedBrowserTransport


class ZhihuTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class ZhihuSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


_MANIFEST = SourceManifest(
    source_id=SourceId.ZHIHU,
    display_name="Zhihu",
    **source_form_schema_fields(ZhihuSettings, accepts_credentials=False),
    capabilities=frozenset(
        {
            SourceCapability.AUTHENTICATION,
            SourceCapability.BOOTSTRAP_IMPORT,
            SourceCapability.ACTIVITY_COLLECTION,
            SourceCapability.SEARCH,
            SourceCapability.TRENDING_FEED,
            SourceCapability.CREATOR_DISCOVERY,
            SourceCapability.RELATED_DISCOVERY,
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
            requires_auth=True,
            transport_kind=SourceTransportKind.BROWSER,
        ),
        operation_spec(
            SourceOperation.TRENDING,
            SourceCapability.TRENDING_FEED,
            requires_auth=True,
            transport_kind=SourceTransportKind.BROWSER,
        ),
        operation_spec(
            SourceOperation.FEED,
            SourceCapability.TRENDING_FEED,
            requires_auth=True,
            transport_kind=SourceTransportKind.BROWSER,
        ),
        operation_spec(
            SourceOperation.CREATOR,
            SourceCapability.CREATOR_DISCOVERY,
            requires_auth=True,
            transport_kind=SourceTransportKind.BROWSER,
        ),
        operation_spec(
            SourceOperation.RELATED,
            SourceCapability.RELATED_DISCOVERY,
            requires_auth=True,
            transport_kind=SourceTransportKind.BROWSER,
        ),
    ),
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


def build_zhihu_connector(
    task_service: object, settings: ZhihuSettings | None = None
) -> ZhihuConnector:
    return ZhihuConnector(
        QueuedBrowserTransport(task_service, SourceId.ZHIHU),  # type: ignore[arg-type]
        settings,
    )


def _external_id(row: dict[str, Any]) -> str:
    raw_id = first_text(row.get("id"), row.get("content_id"), row.get("answer_id"))
    content_type = first_text(row.get("type"), row.get("content_type")) or "answer"
    return f"{content_type}:{raw_id}" if raw_id else ""


def _url(row: dict[str, Any], external_id: str) -> str:
    explicit = first_text(row.get("url"), row.get("content_url"))
    if explicit:
        return explicit
    question_id = first_text(row.get("question_id"), nested(row, "question", "id"))
    content_type = first_text(row.get("type"), row.get("content_type"))
    raw_id = external_id.split(":", 1)[-1]
    if content_type == "answer" and question_id:
        return f"https://www.zhihu.com/question/{question_id}/answer/{raw_id}"
    return f"https://www.zhihu.com/{content_type or 'question'}/{raw_id}"


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
        summary=first_text(
            row.get("summary"), row.get("excerpt"), row.get("content"), row.get("description")
        ),
        creator=first_text(row.get("author"), nested(row, "author", "name"), row.get("author_name"))
        or None,
        published_at=timestamp(row.get("created_time") or row.get("published_at")),
        media_type=media_type,
    )


def _activity(row: dict[str, Any]) -> ActivityEvent | None:
    external_id = _external_id(row)
    if not external_id:
        return None
    return activity_event(
        source_id="zhihu",
        kind=_zhihu_activity_kind(row),
        external_id=external_id,
        occurred_at=timestamp(
            row.get("interaction_time") or row.get("occurred_at") or row.get("created_time")
        ),
        url=_url(row, external_id),
        title=first_text(row.get("title"), nested(row, "question", "title")) or None,
        metadata={"scope": first_text(row.get("scope"))},
    )


def _zhihu_activity_kind(row: dict[str, Any]):  # type: ignore[no-untyped-def]
    action = first_text(row.get("interaction_action"))
    if action.startswith(("赞同了", "喜欢了")):
        return activity_kind("like")
    if action.startswith("收藏了"):
        return activity_kind("favorite")
    return activity_kind(row.get("scope") or row.get("event_type"))
