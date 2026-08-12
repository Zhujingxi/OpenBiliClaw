"""Typed Bangumi client and concrete official v0 API transport."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlencode

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.infrastructure.http.clients import HttpClientFactory

from .models import BangumiPage, BangumiSubject

_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_TYPE_NAMES = {1: "book", 2: "anime", 3: "music", 4: "game", 6: "real"}


class BangumiTransport(Protocol):
    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes: ...


def _mapping(value: JsonValue | None) -> dict[str, JsonValue] | None:
    return value if isinstance(value, dict) else None


def _integer(value: JsonValue | None) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _number(value: JsonValue | None) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _published(value: JsonValue | None) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime(1970, 1, 1, tzinfo=UTC)


def _subject(row: dict[str, JsonValue]) -> BangumiSubject:
    subject_type = _TYPE_NAMES.get(_integer(row.get("type")))
    if subject_type is None:
        raise ValueError("unsupported subject type")
    name = row.get("name")
    name_cn = row.get("name_cn")
    title = name_cn if isinstance(name_cn, str) and name_cn.strip() else name
    rating = _mapping(row.get("rating")) or {}
    images = _mapping(row.get("images")) or {}
    image = images.get("large") or images.get("common")
    return BangumiSubject(
        id=_integer(row.get("id")),
        title=title if isinstance(title, str) else "",
        original_title=name if isinstance(name, str) else "",
        summary=str(row.get("summary") or "")[:20_000],
        creator=None,
        published_at=_published(row.get("date")),
        subject_type=subject_type,
        image_url=image if isinstance(image, str) else None,
        score=_number(rating.get("score")),
        rating_count=_integer(rating.get("total")),
        collection_count=_integer(row.get("collection_total")),
        availability="available",
    )


class HttpxBangumiTransport:
    """Official Bangumi v0 API boundary with scoped client lifetime."""

    _BASE = "https://api.bgm.tv"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._factory = HttpClientFactory()
        self._transport = transport

    @property
    def open_client_count(self) -> int:
        return self._factory.open_client_count

    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes:
        offset = max(0, int(cursor))
        page_limit = min(50, max(1, limit))
        if operation == "search":
            path = "/v0/search/subjects"
            query = urlencode({"limit": page_limit, "offset": offset})
            method = "POST"
            body = json.dumps(
                {
                    "keyword": argument,
                    "sort": "match",
                    "filter": {"type": [1, 2, 3, 4, 6], "nsfw": False},
                }
            ).encode()
        elif operation == "fetch":
            path, query, method, body = f"/v0/subjects/{argument}", "", "GET", None
        else:
            path = "/v0/subjects"
            query = urlencode(
                {
                    "type": 2,
                    "sort": argument if argument in {"rank", "date"} else "rank",
                    "limit": page_limit,
                    "offset": offset,
                }
            )
            method, body = "GET", None
        url = f"{self._BASE}{path}" + (f"?{query}" if query else "")
        async with self._factory.client(transport=self._transport) as client:
            try:
                response = await self._factory.request(
                    client,
                    method,
                    url,
                    headers={"accept": "application/json", "content-type": "application/json"},
                    content=body,
                    idempotency_key="bangumi-read" if method == "POST" else None,
                )
            except httpx.TransportError as exc:
                raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
        _check_status(response)
        try:
            parsed = _JSON.validate_json(response.content)
            if operation == "fetch":
                row = _mapping(parsed)
                if row is None:
                    raise ValueError("invalid subject")
                items: tuple[BangumiSubject, ...] = (_subject(row),)
                next_cursor = None
            else:
                envelope = _mapping(parsed)
                rows = envelope.get("data") if envelope is not None else None
                total = _integer(envelope.get("total")) if envelope is not None else 0
                if not isinstance(rows, list):
                    raise ValueError("invalid page")
                items = tuple(
                    _subject(row)
                    for value in rows[:page_limit]
                    if (row := _mapping(value)) is not None
                )
                next_offset = offset + page_limit
                next_cursor = str(next_offset) if next_offset < total else None
            page = BangumiPage(items=items, next_cursor=next_cursor)
        except (ValidationError, ValueError, TypeError) as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
        return page.model_dump_json().encode()


class BangumiClient:
    def __init__(self, transport: BangumiTransport) -> None:
        self._transport = transport

    async def page(self, operation: str, argument: str, cursor: str, limit: int) -> BangumiPage:
        try:
            raw = await self._transport(operation, argument, cursor, limit)
            return BangumiPage.model_validate_json(raw)
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
