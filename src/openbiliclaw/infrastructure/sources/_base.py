"""Private normalization primitives shared only by source infrastructure adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid5

from pydantic import HttpUrl

from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind
from openbiliclaw.features.feed.domain import ContentItem
from openbiliclaw.features.sources.domain import (
    SourceCapability,
    SourceManifest,
    SourceOperation,
    SourceOperationSpec,
    SourceResult,
    SourceResultKind,
    SourceTransportKind,
    UnsupportedSourceOperationError,
    browser_operation_schemas,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

_CONTENT_NAMESPACE = UUID("a34e58ba-b5b4-42f4-a71f-e0ff95607ff2")
_ACTIVITY_NAMESPACE = UUID("129f9073-232f-46bd-975e-d9897b14bb45")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class RetainedSourceTransport(Protocol):
    """Private raw transport seam; its mappings never cross the connector boundary."""

    async def fetch(
        self, *, operation: str, query: str | None, limit: int
    ) -> list[dict[str, Any]]: ...


class RoutedTransport:
    """Route operations to explicitly supplied production transports."""

    def __init__(self, routes: Mapping[str, RetainedSourceTransport]) -> None:
        self._routes = dict(routes)

    async def fetch(self, *, operation: str, query: str | None, limit: int) -> list[dict[str, Any]]:
        try:
            transport = self._routes[operation]
        except KeyError as exc:
            raise UnsupportedSourceOperationError(f"no transport for {operation}") from exc
        return await transport.fetch(operation=operation, query=query, limit=limit)


def operation_spec(
    operation: SourceOperation,
    capability: SourceCapability,
    *,
    result_kind: SourceResultKind = SourceResultKind.CONTENT,
    requires_auth: bool,
    transport_kind: SourceTransportKind,
    fallback_transport_kind: SourceTransportKind | None = None,
) -> SourceOperationSpec:
    """Keep source manifests concise while retaining explicit per-operation metadata."""

    request_schema, result_schema = browser_operation_schemas(operation)
    return SourceOperationSpec(
        operation=operation,
        capability=capability,
        result_kind=result_kind,
        requires_auth=requires_auth,
        transport_kind=transport_kind,
        fallback_transport_kind=fallback_transport_kind,
        request_schema=request_schema,
        result_schema=result_schema,
    )


class NormalizingConnector:
    """Dispatch retained read-only operations and contain all raw payloads."""

    def __init__(
        self,
        *,
        manifest: SourceManifest,
        transport: RetainedSourceTransport,
        settings: object,
        normalize_content: Callable[[dict[str, Any]], ContentItem | None],
        normalize_activity: Callable[[dict[str, Any]], ActivityEvent | None] | None,
    ) -> None:
        self._manifest = manifest
        self._transport = transport
        self.settings = settings
        self._normalize_content = normalize_content
        self._normalize_activity = normalize_activity

    @property
    def manifest(self) -> SourceManifest:
        return self._manifest

    async def import_activity(self, limit: int = 100) -> tuple[ActivityEvent, ...]:
        operation = SourceOperation.BOOTSTRAP_IMPORT
        self._require(operation)
        if not 1 <= limit <= 100:
            raise ValueError("source activity limit must be between 1 and 100")
        if self._normalize_activity is None:
            raise UnsupportedSourceOperationError(
                f"{self.manifest.source_id} does not support {operation.value}"
            )
        rows = await self._transport.fetch(operation=operation.value, query=None, limit=limit)
        normalized = (self._normalize_activity(row) for row in rows)
        return _dedupe_events(event for event in normalized if event is not None)[:limit]

    async def discover(
        self, operation: SourceOperation, query: str | None, limit: int
    ) -> tuple[ContentItem, ...]:
        spec = self._require(operation)
        if spec.result_kind is SourceResultKind.ACTIVITY:
            raise UnsupportedSourceOperationError("bootstrap_import must use import_activity()")
        if not 1 <= limit <= 100:
            raise ValueError("source discovery limit must be between 1 and 100")
        resolved_query = query.strip() if query else None
        if operation.requires_input and not resolved_query:
            raise ValueError(f"{operation.value} requires a non-empty input")
        rows = await self._transport.fetch(
            operation=operation.value,
            query=resolved_query,
            limit=limit,
        )
        normalized = (self._normalize_content(row) for row in rows)
        return _dedupe_content(item for item in normalized if item is not None)[:limit]

    async def execute(
        self, operation: SourceOperation, query: str | None = None, limit: int = 20
    ) -> SourceResult:
        spec = self._require(operation)
        if spec.result_kind is SourceResultKind.ACTIVITY:
            return await self.import_activity(limit)
        return await self.discover(operation, query, limit)

    def _require(self, operation: SourceOperation):  # type: ignore[no-untyped-def]
        return self.manifest.operation_spec(operation)


def content_item(
    *,
    source_id: str,
    external_id: str,
    url: str,
    title: str,
    summary: str = "",
    creator: str | None = None,
    published_at: datetime | None = None,
    media_type: str = "link",
    metadata: Mapping[str, Any] | None = None,
) -> ContentItem:
    """Build immutable normalized content with a deterministic source identity."""

    return ContentItem(
        id=uuid5(_CONTENT_NAMESPACE, f"{source_id}\0{external_id}"),
        source_id=source_id,
        external_id=external_id,
        url=HttpUrl(url),
        title=title or external_id,
        summary=summary,
        creator=creator,
        published_at=published_at,
        media_type=media_type,
        metadata=dict(metadata or {}),
    )


def activity_event(
    *,
    source_id: str,
    kind: ActivityKind,
    external_id: str,
    occurred_at: datetime | None = None,
    url: str | None = None,
    title: str | None = None,
    text_value: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ActivityEvent:
    """Build immutable activity with a deterministic identity for idempotent imports."""

    timestamp = occurred_at or _EPOCH
    identity = f"{source_id}\0{kind.value}\0{external_id}\0{timestamp.isoformat()}"
    event_metadata = dict(metadata or {})
    if occurred_at is None:
        event_metadata["occurred_at_missing"] = True
    return ActivityEvent(
        id=uuid5(_ACTIVITY_NAMESPACE, identity),
        source_id=source_id,
        kind=kind,
        occurred_at=timestamp,
        content_external_id=external_id,
        url=HttpUrl(url) if url else None,
        title=title,
        text=text_value,
        metadata=event_metadata,
    )


def text(value: object) -> str:
    """Extract common SDK/DOM text shapes without returning their raw structure."""

    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        for key in ("simpleText", "text", "name", "nickname", "title"):
            candidate = text(value.get(key))
            if candidate:
                return candidate
        runs = value.get("runs")
        if isinstance(runs, list):
            return "".join(text(run) for run in runs if isinstance(run, dict)).strip()
    return ""


def nested(row: Mapping[str, Any], *path: str) -> object:
    value: object = row
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def timestamp(value: object) -> datetime | None:
    """Normalize explicit ISO/Unix timestamps; absence remains absent."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def activity_kind(value: object, *, default: ActivityKind = ActivityKind.IMPORT) -> ActivityKind:
    aliases = {
        "history": ActivityKind.VIEW,
        "read_history": ActivityKind.VIEW,
        "view": ActivityKind.VIEW,
        "watched": ActivityKind.VIEW,
        "like": ActivityKind.LIKE,
        "liked": ActivityKind.LIKE,
        "upvoted": ActivityKind.LIKE,
        "favorite": ActivityKind.FAVORITE,
        "favorites": ActivityKind.FAVORITE,
        "saved": ActivityKind.FAVORITE,
        "collection": ActivityKind.FAVORITE,
        "collect": ActivityKind.FAVORITE,
        "follow": ActivityKind.FOLLOW,
        "following": ActivityKind.FOLLOW,
        "subscriptions": ActivityKind.FOLLOW,
        "subscribed": ActivityKind.FOLLOW,
        "search": ActivityKind.SEARCH,
    }
    normalized = text(value).casefold()
    for suffix, kind in aliases.items():
        if normalized == suffix or normalized.endswith(f"_{suffix}"):
            return kind
    return default


def first_text(*values: object) -> str:
    for value in values:
        candidate = text(value)
        if candidate:
            return candidate
    return ""


def _dedupe_content(items: Iterable[ContentItem]) -> tuple[ContentItem, ...]:
    seen: set[str] = set()
    result: list[ContentItem] = []
    for item in items:
        if item.external_id in seen:
            continue
        seen.add(item.external_id)
        result.append(item)
    return tuple(result)


def _dedupe_events(items: Iterable[ActivityEvent]) -> tuple[ActivityEvent, ...]:
    seen: set[UUID] = set()
    result: list[ActivityEvent] = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        result.append(item)
    return tuple(result)
