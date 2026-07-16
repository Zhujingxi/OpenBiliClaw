"""Read-only X/Twitter connector around the retained twitter-cli transport."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.features.feed.domain import ContentItem
from openbiliclaw.features.sources.domain import SourceCapability, SourceManifest
from openbiliclaw.infrastructure.sources._base import (
    NormalizingConnector,
    content_item,
    first_text,
    nested,
    timestamp,
)


class TwitterTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class TwitterSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = False
    mode: Literal["cookie"] = "cookie"
    daily_search_budget: int = Field(default=0, ge=0)
    daily_feed_budget: int = Field(default=0, ge=0)
    daily_creator_budget: int = Field(default=0, ge=0)
    request_interval_seconds: int = Field(default=3, ge=1)
    min_interval_minutes: int = Field(default=60, ge=1)


_MANIFEST = SourceManifest(
    source_id="twitter",
    display_name="X (Twitter)",
    capabilities=frozenset(
        {
            SourceCapability.SEARCH,
            SourceCapability.RECOMMENDED,
            SourceCapability.CREATOR,
        }
    ),
    requires_account=True,
)


class TwitterConnector(NormalizingConnector):
    def __init__(
        self, transport: TwitterTransport, settings: TwitterSettings | None = None
    ) -> None:
        super().__init__(
            manifest=_MANIFEST,
            transport=transport,
            settings=settings or TwitterSettings(),
            normalize_content=_content,
            normalize_activity=None,
        )


def _content(row: dict[str, Any]) -> ContentItem | None:
    external_id = first_text(row.get("rest_id"), row.get("id_str"), row.get("id"))
    if not external_id:
        return None
    creator = first_text(
        nested(row, "user", "screen_name"),
        nested(row, "author", "username"),
        row.get("username"),
    )
    body = first_text(row.get("full_text"), row.get("text"), row.get("body"))
    return content_item(
        source_id="twitter",
        external_id=external_id,
        url=first_text(row.get("url")) or f"https://x.com/{creator or 'i'}/status/{external_id}",
        title=body[:200] or external_id,
        summary=body,
        creator=creator or None,
        published_at=timestamp(row.get("created_at") or row.get("published_at")),
        media_type="tweet",
    )
