"""Read-only Reddit connector around retained rdt-cli or extension transports."""

from __future__ import annotations

import asyncio
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
)
from openbiliclaw.infrastructure.sources._base import (
    NormalizingConnector,
    RoutedTransport,
    activity_event,
    activity_kind,
    content_item,
    first_text,
    operation_spec,
    timestamp,
)
from openbiliclaw.infrastructure.sources.browser_tasks import QueuedBrowserTransport
from openbiliclaw.sources.reddit_tasks import (
    CommandRunner,
    build_reddit_command,
    run_reddit_command,
)


class RedditTransport(Protocol):
    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class RedditCliTransport:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner

    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        modes = {
            SourceOperation.SEARCH.value: "search",
            SourceOperation.TRENDING.value: "hot",
            SourceOperation.COMMUNITY.value: "subreddit",
            SourceOperation.RELATED.value: "related",
        }
        try:
            mode = modes[operation]
        except KeyError as exc:
            raise ValueError(f"unsupported Reddit CLI operation: {operation}") from exc
        args = build_reddit_command("rdt", mode=mode, query=query or "", limit=limit)
        return await asyncio.to_thread(run_reddit_command, args, runner=self._runner)


class RedditSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    enabled: bool = False
    backend: Literal["rdt", "extension"] = "rdt"
    source_modes: tuple[Literal["search", "hot", "subreddit", "related"], ...] = (
        "search",
        "hot",
        "subreddit",
        "related",
    )
    daily_search_budget: int = Field(default=300, ge=0)
    daily_hot_budget: int = Field(default=300, ge=0)
    daily_subreddit_budget: int = Field(default=300, ge=0)
    daily_related_budget: int = Field(default=300, ge=0)
    request_interval_seconds: int = Field(default=3, ge=1)
    min_interval_minutes: int = Field(default=60, ge=1)


class RedditConnector(NormalizingConnector):
    def __init__(self, transport: RedditTransport, settings: RedditSettings | None = None) -> None:
        resolved = settings or RedditSettings()
        discovery_kind = (
            SourceTransportKind.CLI if resolved.backend == "rdt" else SourceTransportKind.BROWSER
        )
        super().__init__(
            manifest=SourceManifest(
                source_id=SourceId.REDDIT,
                display_name="Reddit",
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
                        transport_kind=discovery_kind,
                    ),
                    operation_spec(
                        SourceOperation.TRENDING,
                        SourceCapability.TRENDING_FEED,
                        requires_auth=True,
                        transport_kind=discovery_kind,
                    ),
                    operation_spec(
                        SourceOperation.COMMUNITY,
                        SourceCapability.COMMUNITY_DISCOVERY,
                        requires_auth=True,
                        transport_kind=discovery_kind,
                    ),
                    operation_spec(
                        SourceOperation.RELATED,
                        SourceCapability.RELATED_DISCOVERY,
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


def build_reddit_connector(
    *,
    task_service: object,
    runner: CommandRunner | None = None,
    settings: RedditSettings | None = None,
) -> RedditConnector:
    resolved = settings or RedditSettings()
    browser = QueuedBrowserTransport(task_service, SourceId.REDDIT)  # type: ignore[arg-type]
    discovery: RedditTransport = (
        RedditCliTransport(runner) if resolved.backend == "rdt" else browser
    )
    return RedditConnector(
        RoutedTransport(
            {
                SourceOperation.BOOTSTRAP_IMPORT.value: browser,
                SourceOperation.SEARCH.value: discovery,
                SourceOperation.TRENDING.value: discovery,
                SourceOperation.COMMUNITY.value: discovery,
                SourceOperation.RELATED.value: discovery,
            }
        ),
        resolved,
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
