"""Typed V2EX client and concrete official public API transport."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlencode

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.infrastructure.http.clients import HttpClientFactory

from .models import V2EXMember, V2EXNode, V2EXPage, V2EXTopic

_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class V2EXTransport(Protocol):
    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes: ...


def _mapping(value: JsonValue | None) -> dict[str, JsonValue] | None:
    return value if isinstance(value, dict) else None


def _integer(value: JsonValue | None) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _topic(row: dict[str, JsonValue]) -> V2EXTopic:
    member = _mapping(row.get("member")) or {}
    node = _mapping(row.get("node")) or {}
    created = _integer(row.get("created"))
    return V2EXTopic(
        id=_integer(row.get("id")),
        title=str(row.get("title") or ""),
        content=str(row.get("content") or row.get("content_rendered") or "")[:20_000],
        member=V2EXMember(username=str(member.get("username") or "")),
        published_at=datetime.fromtimestamp(max(0, created), UTC),
        node=V2EXNode(name=str(node.get("name") or ""), title=str(node.get("title") or "")),
        reply_count=_integer(row.get("replies")),
        availability="available",
    )


class HttpxV2EXTransport:
    """Read-only legacy/public V2EX JSON API boundary."""

    _BASE = "https://www.v2ex.com"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._factory = HttpClientFactory()
        self._transport = transport

    @property
    def open_client_count(self) -> int:
        return self._factory.open_client_count

    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes:
        offset = max(0, int(cursor))
        if operation == "fetch":
            path = "/api/topics/show.json"
            query = urlencode({"id": argument})
        elif operation == "creator":
            path = "/api/topics/show.json"
            query = urlencode({"username": argument})
        elif operation == "feed" and argument == "latest":
            path, query = "/api/topics/latest.json", ""
        else:
            # V2EX has no official full-text search endpoint. Search and the
            # default hot feed use the bounded official hot response; the
            # provider applies query filtering in a future discovery strategy.
            path, query = "/api/topics/hot.json", ""
        url = f"{self._BASE}{path}" + (f"?{query}" if query else "")
        async with self._factory.client(transport=self._transport) as client:
            try:
                response = await self._factory.request(client, "GET", url)
            except httpx.TransportError as exc:
                raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
        _check_status(response)
        try:
            parsed = _JSON.validate_json(response.content)
            if isinstance(parsed, list):
                rows = parsed
            elif (row := _mapping(parsed)) is not None:
                rows = [row]
            else:
                raise ValueError("invalid response")
            all_items = tuple(_topic(row) for value in rows if (row := _mapping(value)) is not None)
            if operation == "search":
                needle = argument.strip().casefold()
                all_items = tuple(
                    item
                    for item in all_items
                    if needle in f"{item.title}\n{item.content}".casefold()
                )
            page_limit = min(50, max(1, limit))
            items = all_items[offset : offset + page_limit]
            next_offset = offset + len(items)
            page = V2EXPage(
                items=items,
                next_cursor=str(next_offset) if next_offset < len(all_items) else None,
            )
        except (ValidationError, ValueError, TypeError, OSError) as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
        return page.model_dump_json().encode()


class V2EXClient:
    def __init__(self, transport: V2EXTransport) -> None:
        self._transport = transport

    async def page(self, operation: str, argument: str, cursor: str, limit: int) -> V2EXPage:
        try:
            raw = await self._transport(operation, argument, cursor, limit)
            return V2EXPage.model_validate_json(raw)
        except ContentIntegrationError:
            raise
        except (ValidationError, ValueError) as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc


def _check_status(response: httpx.Response) -> None:
    if response.status_code == 429:
        raise _error(IntegrationErrorCode.RATE_LIMITED)
    if response.status_code in {401, 403}:
        raise _error(IntegrationErrorCode.ACCESS_DENIED)
    if response.status_code >= 400:
        raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE)


def _error(code: IntegrationErrorCode) -> ContentIntegrationError:
    message = (
        "provider rate limited"
        if code is IntegrationErrorCode.RATE_LIMITED
        else "provider request failed"
    )
    return ContentIntegrationError(code, message)
