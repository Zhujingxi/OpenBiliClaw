"""Read-only Reddit connector using the authenticated browser extension."""

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
    operation_spec,
    timestamp,
)
from openbiliclaw.infrastructure.sources.browser_tasks import QueuedBrowserTransport


class RedditTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class RedditSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class RedditConnector(NormalizingConnector):
    def __init__(self, transport: RedditTransport, settings: RedditSettings | None = None) -> None:
        resolved = settings or RedditSettings()
        super().__init__(
            manifest=SourceManifest(
                source_id=SourceId.REDDIT,
                display_name="Reddit",
                **source_form_schema_fields(RedditSettings, accepts_credentials=False),
                capabilities=frozenset(
                    {
                        SourceCapability.AUTHENTICATION,
                        SourceCapability.BOOTSTRAP_IMPORT,
                        SourceCapability.ACTIVITY_COLLECTION,
                        SourceCapability.SEARCH,
                        SourceCapability.TRENDING_FEED,
                        SourceCapability.COMMUNITY_DISCOVERY,
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
                        SourceOperation.COMMUNITY,
                        SourceCapability.COMMUNITY_DISCOVERY,
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
            ),
            transport=transport,
            settings=resolved,
            normalize_content=_content,
            normalize_activity=_activity,
        )


def build_reddit_connector(
    *,
    task_service: object,
    settings: RedditSettings | None = None,
) -> RedditConnector:
    resolved = settings or RedditSettings()
    browser = QueuedBrowserTransport(task_service, SourceId.REDDIT)  # type: ignore[arg-type]
    return RedditConnector(browser, resolved)


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
