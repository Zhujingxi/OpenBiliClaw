"""Read-only Douyin connector around direct HTTP or logged-in browser transports."""

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
    RoutedTransport,
    activity_event,
    activity_kind,
    content_item,
    first_text,
    nested,
    operation_spec,
    timestamp,
)
from openbiliclaw.infrastructure.sources.browser_tasks import QueuedBrowserTransport


class DouyinTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class DouyinReadClient(Protocol):
    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, Any]]: ...
    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, Any]]: ...
    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, Any]]: ...


class DouyinDirectTransport:
    def __init__(self, client: DouyinReadClient) -> None:
        self._client = client

    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == SourceOperation.SEARCH:
            return await self._client.search_aweme(query or "", limit=limit)
        if operation == SourceOperation.TRENDING:
            return await self._client.get_hot_board(limit=limit)
        if operation == SourceOperation.FEED:
            return await self._client.get_recommend_feed(limit=limit)
        raise ValueError(f"unsupported Douyin operation: {operation}")


class DouyinSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = False
    mode: Literal["direct", "extension"] = "direct"
    daily_search_budget: int = Field(default=0, ge=0)
    daily_hot_budget: int = Field(default=0, ge=0)
    daily_feed_budget: int = Field(default=0, ge=0)
    request_interval_seconds: int = Field(default=2, ge=1)


class DouyinConnector(NormalizingConnector):
    def __init__(self, transport: DouyinTransport, settings: DouyinSettings | None = None) -> None:
        resolved = settings or DouyinSettings()
        discovery_kind = (
            SourceTransportKind.DIRECT if resolved.mode == "direct" else SourceTransportKind.BROWSER
        )
        super().__init__(
            manifest=SourceManifest(
                source_id=SourceId.DOUYIN,
                display_name="Douyin",
                **source_form_schema_fields(DouyinSettings),
                capabilities=frozenset(
                    {
                        SourceCapability.AUTHENTICATION,
                        SourceCapability.BOOTSTRAP_IMPORT,
                        SourceCapability.ACTIVITY_COLLECTION,
                        SourceCapability.SEARCH,
                        SourceCapability.TRENDING_FEED,
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
                        transport_kind=discovery_kind,
                    ),
                    operation_spec(
                        SourceOperation.TRENDING,
                        SourceCapability.TRENDING_FEED,
                        requires_auth=True,
                        transport_kind=discovery_kind,
                    ),
                    operation_spec(
                        SourceOperation.FEED,
                        SourceCapability.TRENDING_FEED,
                        requires_auth=True,
                        transport_kind=discovery_kind,
                    ),
                ),
            ),
            transport=transport,
            settings=resolved,
            normalize_content=_content,
            normalize_activity=_activity,
        )


def build_douyin_connector(
    *,
    task_service: object,
    direct_client: DouyinReadClient | None = None,
    settings: DouyinSettings | None = None,
) -> DouyinConnector:
    resolved = settings or DouyinSettings()
    browser = QueuedBrowserTransport(task_service, SourceId.DOUYIN)  # type: ignore[arg-type]
    discovery: DouyinTransport
    if resolved.mode == "direct":
        if direct_client is None:
            raise ValueError("direct_client is required in direct mode")
        discovery = DouyinDirectTransport(direct_client)
    else:
        discovery = browser
    return DouyinConnector(
        RoutedTransport(
            {
                SourceOperation.BOOTSTRAP_IMPORT.value: browser,
                SourceOperation.SEARCH.value: discovery,
                SourceOperation.TRENDING.value: discovery,
                SourceOperation.FEED.value: discovery,
            }
        ),
        resolved,
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
