"""Read-only Bilibili connector around retained API/extension transports."""

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
)
from openbiliclaw.infrastructure.sources._base import (
    NormalizingConnector,
    activity_event,
    activity_kind,
    content_item,
    first_text,
    nested,
    operation_spec,
    text,
    timestamp,
)


class BilibiliTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class BilibiliReadClient(Protocol):
    async def search(
        self, keyword: str, *, page: int = 1, page_size: int = 20, order: str = "totalrank"
    ) -> list[dict[str, Any]]: ...
    async def get_user_history(self, max_items: int = 100) -> list[dict[str, Any]]: ...
    async def get_all_favorites(
        self,
        *,
        max_folders: int = 10,
        max_items_per_folder: int = 50,
        max_total_items: int | None = None,
    ) -> list[Any]: ...
    async def get_following(self, *, page: int = 1, page_size: int = 50) -> list[Any]: ...
    async def get_related_videos(self, bvid: str) -> list[dict[str, Any]]: ...
    async def get_ranking(self, rid: int = 0) -> list[dict[str, Any]]: ...


class BilibiliDirectTransport:
    def __init__(self, client: BilibiliReadClient) -> None:
        self._client = client

    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        if operation == SourceOperation.BOOTSTRAP_IMPORT:
            history = [
                dict(row, event_type=row.get("event_type") or "view")
                for row in await self._client.get_user_history(max_items=limit)
            ]
            folders = await self._client.get_all_favorites(max_total_items=limit)
            favorites = [
                dict(row, event_type="favorite")
                for folder in folders
                for row in getattr(folder, "items", ())
                if isinstance(row, dict)
            ]
            following = [
                {
                    "id": f"user:{getattr(user, 'mid', '')}",
                    "title": str(getattr(user, "uname", "")),
                    "url": f"https://space.bilibili.com/{getattr(user, 'mid', '')}",
                    "event_type": "follow",
                }
                for user in await self._client.get_following(page_size=limit)
                if getattr(user, "mid", None)
            ]
            return (history + favorites + following)[:limit]
        if operation == SourceOperation.SEARCH:
            return await self._client.search(query or "", page_size=limit)
        if operation == SourceOperation.TRENDING:
            return (await self._client.get_ranking())[:limit]
        if operation == SourceOperation.RELATED:
            return (await self._client.get_related_videos(query or ""))[:limit]
        raise ValueError(f"unsupported Bilibili operation: {operation}")


def build_bilibili_connector(
    client: BilibiliReadClient, settings: BilibiliSettings | None = None
) -> BilibiliConnector:
    return BilibiliConnector(BilibiliDirectTransport(client), settings)


class BilibiliSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = True


_MANIFEST = SourceManifest(
    source_id=SourceId.BILIBILI,
    display_name="Bilibili",
    capabilities=frozenset(
        {
            SourceCapability.AUTHENTICATION,
            SourceCapability.BOOTSTRAP_IMPORT,
            SourceCapability.ACTIVITY_COLLECTION,
            SourceCapability.SEARCH,
            SourceCapability.TRENDING_FEED,
            SourceCapability.RELATED_DISCOVERY,
        }
    ),
    operations=(
        operation_spec(
            SourceOperation.BOOTSTRAP_IMPORT,
            SourceCapability.BOOTSTRAP_IMPORT,
            result_kind=SourceResultKind.ACTIVITY,
            requires_auth=True,
            transport_kind=SourceTransportKind.DIRECT,
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
            SourceOperation.RELATED,
            SourceCapability.RELATED_DISCOVERY,
            requires_auth=False,
            transport_kind=SourceTransportKind.DIRECT,
        ),
    ),
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
