"""Typed Hacker News client and official Firebase API transport."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.infrastructure.http.clients import HttpClientFactory

from .models import HackerNewsItem, HackerNewsItemType, HackerNewsPage

_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class HackerNewsTransport(Protocol):
    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes: ...


class _TextExtractor(HTMLParser):
    _IGNORED = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self._IGNORED:
            self._ignored_depth += 1
        elif tag in {"br", "p"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._IGNORED and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag == "p":
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _plain_text(value: JsonValue | None) -> str:
    if not isinstance(value, str):
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return " ".join("".join(parser.parts).split())


def _mapping(value: JsonValue | None) -> dict[str, JsonValue] | None:
    return value if isinstance(value, dict) else None


def _integer(value: JsonValue | None) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _external_url(value: JsonValue | None) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    return value if parsed.scheme in {"http", "https"} and parsed.hostname is not None else None


def _item(value: JsonValue) -> HackerNewsItem | None:
    row = _mapping(value)
    if row is None or row.get("deleted") is True or row.get("dead") is True:
        return None
    item_type = row.get("type")
    title = _plain_text(row.get("title"))[:500]
    item_id = _integer(row.get("id"))
    published = _integer(row.get("time"))
    if (
        not isinstance(item_type, str)
        or item_type not in {"story", "job", "poll"}
        or not title
        or item_id <= 0
        or published <= 0
    ):
        return None
    author = row.get("by")
    normalized_author = author.strip()[:128] if isinstance(author, str) else ""
    external_url = _external_url(row.get("url"))
    return HackerNewsItem(
        id=item_id,
        item_type=HackerNewsItemType(item_type),
        title=title,
        body=_plain_text(row.get("text"))[:20_000],
        author=normalized_author or None,
        published_at=datetime.fromtimestamp(published, UTC),
        score=min(2_147_483_647, max(0, _integer(row.get("score")))),
        comment_count=min(2_147_483_647, max(0, _integer(row.get("descendants")))),
        external_url=external_url,
    )


class HttpxHackerNewsTransport:
    """Anonymous read-only Hacker News Firebase API boundary."""

    _BASE = "https://hacker-news.firebaseio.com/v0"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._factory = HttpClientFactory()
        self._transport = transport

    @property
    def open_client_count(self) -> int:
        return self._factory.open_client_count

    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes:
        try:
            offset = max(0, int(cursor))
        except ValueError as exc:
            raise _error(IntegrationErrorCode.INVALID_CONTENT_REF) from exc
        async with self._factory.client(transport=self._transport) as client:
            try:
                if operation == "fetch":
                    value = await self._get(client, f"/item/{argument}.json", not_found=True)
                    values = [value]
                    next_cursor = None
                elif operation == "feed" and argument == "top":
                    raw_ids = await self._get(client, "/topstories.json")
                    if not isinstance(raw_ids, list) or any(
                        not isinstance(item_id, int) or isinstance(item_id, bool) or item_id <= 0
                        for item_id in raw_ids
                    ):
                        raise ValueError("invalid story list")
                    page_limit = min(100, max(1, limit))
                    ids = raw_ids[offset : offset + page_limit]
                    values = await asyncio.gather(
                        *(self._get(client, f"/item/{item_id}.json") for item_id in ids)
                    )
                    next_offset = offset + len(ids)
                    next_cursor = str(next_offset) if next_offset < len(raw_ids) else None
                else:
                    raise ValueError(f"unsupported operation {operation!r}")
                items = tuple(item for value in values if (item := _item(value)) is not None)
                return (
                    HackerNewsPage(items=items, next_cursor=next_cursor).model_dump_json().encode()
                )
            except ContentIntegrationError:
                raise
            except (ValidationError, ValueError, TypeError, OSError, OverflowError) as exc:
                raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc

    async def _get(
        self, client: httpx.AsyncClient, path: str, *, not_found: bool = False
    ) -> JsonValue:
        try:
            response = await self._factory.request(client, "GET", f"{self._BASE}{path}")
        except httpx.TransportError as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
        if response.status_code == 404 and not_found:
            return None
        _check_status(response)
        try:
            return _JSON.validate_json(response.content)
        except ValidationError as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc


class HackerNewsClient:
    def __init__(self, transport: HackerNewsTransport) -> None:
        self._transport = transport

    async def page(self, operation: str, argument: str, cursor: str, limit: int) -> HackerNewsPage:
        try:
            raw = await self._transport(operation, argument, cursor, limit)
            return HackerNewsPage.model_validate_json(raw)
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
