"""Read-only Xiaohongshu connector around logged-in extension transports."""

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
    activity_event,
    activity_kind,
    content_item,
    first_text,
    nested,
    operation_spec,
    timestamp,
)
from openbiliclaw.infrastructure.sources.browser_tasks import QueuedBrowserTransport


class XiaohongshuTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class XiaohongshuSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = False
    daily_search_budget: int = Field(default=0, ge=0)
    daily_creator_budget: int = Field(default=0, ge=0)
    task_interval_seconds: int = Field(default=45, ge=1)


_MANIFEST = SourceManifest(
    source_id=SourceId.XIAOHONGSHU,
    display_name="Xiaohongshu",
    capabilities=frozenset(
        {
            SourceCapability.AUTHENTICATION,
            SourceCapability.BOOTSTRAP_IMPORT,
            SourceCapability.ACTIVITY_COLLECTION,
            SourceCapability.SEARCH,
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
            requires_auth=True,
            transport_kind=SourceTransportKind.BROWSER,
        ),
        operation_spec(
            SourceOperation.CREATOR,
            SourceCapability.CREATOR_DISCOVERY,
            requires_auth=True,
            transport_kind=SourceTransportKind.BROWSER,
        ),
    ),
)


class XiaohongshuConnector(NormalizingConnector):
    def __init__(
        self, transport: XiaohongshuTransport, settings: XiaohongshuSettings | None = None
    ) -> None:
        super().__init__(
            manifest=_MANIFEST,
            transport=transport,
            settings=settings or XiaohongshuSettings(),
            normalize_content=_content,
            normalize_activity=_activity,
        )


def build_xiaohongshu_connector(
    task_service: object, settings: XiaohongshuSettings | None = None
) -> XiaohongshuConnector:
    return XiaohongshuConnector(
        QueuedBrowserTransport(task_service, SourceId.XIAOHONGSHU),  # type: ignore[arg-type]
        settings,
    )


def _external_id(row: dict[str, Any]) -> str:
    return first_text(row.get("note_id"), row.get("id"), row.get("content_id"))


def _content(row: dict[str, Any]) -> ContentItem | None:
    external_id = _external_id(row)
    if not external_id:
        return None
    return content_item(
        source_id="xiaohongshu",
        external_id=external_id,
        url=first_text(row.get("url"), row.get("note_url"))
        or f"https://www.xiaohongshu.com/explore/{external_id}",
        title=first_text(row.get("title"), row.get("desc")) or external_id,
        summary=first_text(row.get("desc"), row.get("description")),
        creator=first_text(nested(row, "author", "nickname"), row.get("author_name")) or None,
        published_at=timestamp(row.get("published_at") or row.get("time")),
        media_type="note",
    )


def _activity(row: dict[str, Any]) -> ActivityEvent | None:
    external_id = _external_id(row)
    if not external_id:
        return None
    return activity_event(
        source_id="xiaohongshu",
        kind=activity_kind(row.get("scope") or row.get("event_type")),
        external_id=external_id,
        occurred_at=timestamp(row.get("occurred_at")),
        url=first_text(row.get("url")) or f"https://www.xiaohongshu.com/explore/{external_id}",
        title=first_text(row.get("title"), row.get("desc")) or None,
        metadata={"scope": first_text(row.get("scope"))},
    )
