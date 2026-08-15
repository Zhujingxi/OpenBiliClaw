"""Bounded Bilibili HTTP client with strict response validation."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlencode

import httpx
from pydantic import JsonValue, ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.infrastructure.http.clients import HttpClientFactory
from openbiliclaw.infrastructure.http.policy import HttpPolicy

from .models import (
    ITEM_ADAPTER,
    BilibiliActionData,
    BilibiliItem,
    BilibiliNavData,
    BilibiliPageData,
    BilibiliResponse,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.access.models import CredentialAccessHandle


class BilibiliTransport(Protocol):
    async def __call__(
        self,
        method: str,
        path: str,
        query: str,
        cookie: str | None,
        body: bytes,
    ) -> bytes: ...


class CredentialResolver(Protocol):
    async def __call__(self, handle: CredentialAccessHandle) -> str: ...


def cookie_parts(cookie: str) -> tuple[str | None, str | None]:
    values: dict[str, str] = {}
    for segment in cookie.split(";"):
        name, separator, value = segment.strip().partition("=")
        if separator and name in {"SESSDATA", "bili_jct"}:
            values[name] = value.strip()
    return values.get("SESSDATA"), values.get("bili_jct")


class HttpxBilibiliTransport:
    """Real network boundary; tests inject MockTransport or override the base URL."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        base_url: str = "https://api.bilibili.com",
    ) -> None:
        self._factory = HttpClientFactory(
            HttpPolicy(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
                )
            )
        )
        self._transport = transport
        self._base_url = base_url.rstrip("/")

    @property
    def open_client_count(self) -> int:
        return self._factory.open_client_count

    async def __call__(
        self, method: str, path: str, query: str, cookie: str | None, body: bytes
    ) -> bytes:
        headers = {
            "referer": (
                "https://search.bilibili.com/"
                if path == "/x/web-interface/search/type"
                else "https://www.bilibili.com"
            )
        }
        if cookie is not None:
            headers["cookie"] = cookie
        if body:
            headers["content-type"] = "application/x-www-form-urlencoded"
        url = f"{self._base_url}{path}" + (f"?{query}" if query else "")
        async with self._factory.client(transport=self._transport) as client:
            try:
                if cookie is None and path == "/x/web-interface/search/type":
                    await self._factory.request(
                        client,
                        "GET",
                        "https://www.bilibili.com",
                        headers=headers,
                    )

                response = await self._factory.request(
                    client,
                    method,
                    url,
                    headers=headers,
                    content=body or None,
                )
            except httpx.TransportError as exc:
                raise ContentIntegrationError(
                    IntegrationErrorCode.NETWORK_UNAVAILABLE, "provider request failed"
                ) from exc
            if response.status_code in {412, 429}:
                raise ContentIntegrationError(
                    IntegrationErrorCode.RATE_LIMITED, "provider rate limited request"
                )
            if response.status_code >= 400:
                raise ContentIntegrationError(
                    IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider request failed"
                )
            # Validate the envelope immediately, before response bytes cross the boundary.
            try:
                BilibiliResponse.model_validate_json(response.content)
            except ValidationError as exc:
                raise ContentIntegrationError(
                    IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider returned invalid data"
                ) from exc
            return response.content


_TAGS = re.compile(r"<[^>]+>")


def _mapping(value: JsonValue | None) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _integer(value: JsonValue | None) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _text(value: JsonValue | None) -> str:
    return html.unescape(_TAGS.sub("", value)).strip() if isinstance(value, str) else ""


def _identifier(value: JsonValue | None) -> str:
    text = _text(value)
    return text or (str(value) if isinstance(value, int) and not isinstance(value, bool) else "")


def _cover(value: JsonValue | None) -> str | None:
    text = _text(value)
    if not text:
        return None
    if text.startswith("//"):
        return f"https:{text}"
    return text if text.startswith(("http://", "https://")) else None


def _duration(value: JsonValue | None) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    if not isinstance(value, str):
        return 0
    parts = value.split(":")
    if not all(part.isdigit() for part in parts):
        return 0
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _event_timestamp(path: str, row: dict[str, JsonValue]) -> dict[str, JsonValue]:
    field = {
        "/x/web-interface/history/cursor": "view_at",
        "/x/v3/fav/resource/list": "fav_time",
    }.get(path)
    if field is None or not _integer(row.get(field)):
        return row
    return {**row, "pubdate": row[field]}


def _item(row: dict[str, JsonValue]) -> BilibiliItem:
    if row.get("kind") in {"video", "article"}:
        return ITEM_ADAPTER.validate_python(row)
    history = _mapping(row.get("history"))
    bvid = _text(row.get("bvid") or history.get("bvid") or row.get("id"))
    owner = _mapping(row.get("owner"))
    creator_id = _identifier(owner.get("mid") or row.get("mid") or row.get("author_mid"))
    creator_name = _text(owner.get("name") or row.get("author") or row.get("author_name"))
    stat = _mapping(row.get("stat"))
    state = _integer(row.get("state"))
    return ITEM_ADAPTER.validate_python(
        {
            "kind": "video",
            "id": bvid,
            "aid": _integer(row.get("aid") or history.get("oid") or row.get("id")),
            "title": _text(row.get("title")),
            "description": _text(row.get("desc") or row.get("description")),
            "creator": (
                {"id": creator_id, "name": creator_name} if creator_id and creator_name else None
            ),
            "cover_url": _cover(row.get("pic") or row.get("cover")),
            "published_at": max(0, _integer(row.get("pubdate") or row.get("view_at"))),
            "duration_seconds": _duration(row.get("duration")),
            "stats": {
                "views": max(0, _integer(stat.get("view") or row.get("play"))),
                "likes": max(0, _integer(stat.get("like") or row.get("like"))),
                "favorites": max(0, _integer(stat.get("favorite") or row.get("favorites"))),
            },
            "availability": "available" if state >= 0 else "tombstone",
        }
    )


class BilibiliClient:
    """Typed endpoint client; contains no provider-independent policy."""

    def __init__(self, transport: BilibiliTransport, resolver: CredentialResolver) -> None:
        self._transport = transport
        self._resolver = resolver

    def __repr__(self) -> str:
        return "BilibiliClient(credentials=<opaque>)"

    async def page(
        self,
        path: str,
        params: Mapping[str, str | int],
        access: CredentialAccessHandle | None = None,
    ) -> BilibiliPageData:
        payload = await self._request(
            "GET", path, self._endpoint_params(path, params), access=access
        )
        data = _mapping(payload.data)
        rows: JsonValue | None
        if path == "/x/web-interface/archive/related" and isinstance(payload.data, list):
            rows = payload.data
        elif path == "/x/web-interface/search/type":
            rows = data.get("result")
        elif path == "/x/web-interface/wbi/index/top/feed/rcmd":
            rows = data.get("item")
        elif path == "/x/space/wbi/arc/search":
            rows = _mapping(data.get("list")).get("vlist")
        else:
            rows = data.get("list")
        rows = rows if rows is not None else data.get("items")
        if not isinstance(rows, list):
            raise self._invalid()
        try:
            # Search can include legacy archives and rcmd can include ad cards.
            # Rows without a BVID cannot form the provider's stable ContentRef.
            mapped_rows = tuple(
                _event_timestamp(path, row) for value in rows if (row := _mapping(value))
            )
            bvid_required = path in {
                "/x/web-interface/search/type",
                "/x/web-interface/wbi/index/top/feed/rcmd",
            }
            items = tuple(
                _item(row)
                for row in mapped_rows
                if not bvid_required
                or _text(row.get("bvid"))
                or row.get("kind") in {"video", "article"}
            )
            cursor = (
                str(data["next_cursor"])
                if data.get("next_cursor") is not None
                else self._next_cursor(path, data, params)
            )
            return BilibiliPageData(items=items, next_cursor=cursor)
        except (TypeError, ValidationError, ValueError) as exc:
            raise self._invalid() from exc

    async def item(
        self,
        path: str,
        params: Mapping[str, str | int],
        access: CredentialAccessHandle | None = None,
    ) -> BilibiliItem:
        payload = await self._request(
            "GET", path, self._endpoint_params(path, params), access=access
        )
        data = _mapping(payload.data)
        try:
            return _item(data)
        except ValidationError as exc:
            raise self._invalid() from exc

    async def nav_with_cookie(self, cookie: str) -> BilibiliNavData:
        payload = await self._request_with_cookie("GET", "/x/web-interface/nav", {}, cookie, b"")
        try:
            data = _mapping(payload.data)
            return BilibiliNavData.model_validate(
                {
                    "is_login": data.get("isLogin", data.get("is_login")),
                    "mid": data.get("mid"),
                    "name": data.get("uname", data.get("name")),
                }
            )
        except ValidationError as exc:
            raise self._invalid() from exc

    async def action(
        self,
        path: str,
        params: Mapping[str, str | int],
        access: CredentialAccessHandle,
        *,
        idempotency_key: str,
    ) -> BilibiliActionData:
        cookie = await self._resolver(access)
        session, csrf = cookie_parts(cookie)
        if not session or not csrf:
            raise ContentIntegrationError(IntegrationErrorCode.ACCESS_DENIED, "login expired")
        data = dict(params)
        data["csrf"] = csrf
        data["csrf_token"] = csrf
        data["idempotency_key"] = idempotency_key
        body = urlencode(data).encode()
        payload = await self._request_with_cookie("POST", path, {}, cookie, body)
        try:
            return BilibiliActionData.model_validate(payload.data)
        except ValidationError as exc:
            raise self._invalid() from exc

    async def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int],
        *,
        access: CredentialAccessHandle | None,
    ) -> BilibiliResponse:
        cookie = await self._resolver(access) if access is not None else None
        return await self._request_with_cookie(method, path, params, cookie, b"")

    async def _request_with_cookie(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int],
        cookie: str | None,
        body: bytes,
    ) -> BilibiliResponse:
        raw = await self._transport(method, path, urlencode(params), cookie, body)
        try:
            payload = BilibiliResponse.model_validate_json(raw)
        except ValidationError as exc:
            raise self._invalid() from exc
        if payload.code == 0:
            return payload
        if payload.code == -101:
            raise ContentIntegrationError(IntegrationErrorCode.ACCESS_DENIED, "login expired")
        if payload.code in {-412, -429}:
            raise ContentIntegrationError(
                IntegrationErrorCode.RATE_LIMITED, "provider rate limited"
            )
        raise ContentIntegrationError(
            IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider request failed"
        )

    @staticmethod
    def _endpoint_params(path: str, params: Mapping[str, str | int]) -> dict[str, str | int]:
        values = dict(params)
        limit = _integer(values.pop("limit", 20))
        cursor = str(values.pop("cursor", "0"))
        page = int(cursor) if cursor.isdigit() and int(cursor) > 0 else 1
        if path == "/x/web-interface/search/type":
            values.update(search_type="video", page=page, page_size=limit)
        elif path in {"/x/web-interface/popular", "/x/space/wbi/arc/search"}:
            values.update(pn=page, ps=limit)
        elif path == "/x/web-interface/wbi/index/top/feed/rcmd":
            values.update(
                ps=limit,
                fresh_type=3,
                feed_version="V8",
                fresh_idx=1,
                fresh_idx_1h=1,
                brush=0,
                homepage_ver=1,
                web_location=1430650,
            )
        elif path == "/x/web-interface/history/cursor":
            maximum, separator, view_at = cursor.partition(",")
            values.update(
                ps=limit,
                max=maximum if maximum.isdigit() else "0",
                view_at=view_at if separator and view_at.isdigit() else 0,
            )
        elif path == "/x/v3/fav/resource/list":
            values.update(pn=page, ps=limit)
        elif path == "/x/web-interface/view":
            values["bvid"] = values.pop("id")
        return values

    @staticmethod
    def _next_cursor(
        path: str, data: Mapping[str, JsonValue], params: Mapping[str, str | int]
    ) -> str | None:
        if path == "/x/web-interface/popular" and data.get("no_more") is not True:
            cursor = str(params.get("cursor", "0"))
            return str((int(cursor) if cursor.isdigit() else 0) + 1)
        if path == "/x/web-interface/search/type" and data.get("next") is not None:
            return str(_integer(data.get("next")))
        if path == "/x/web-interface/history/cursor":
            cursor_data = _mapping(data.get("cursor"))
            maximum = _integer(cursor_data.get("max"))
            view_at = _integer(cursor_data.get("view_at"))
            return f"{maximum},{view_at}" if maximum and view_at else None
        if path == "/x/v3/fav/resource/list" and data.get("has_more") is True:
            cursor = str(params.get("cursor", "0"))
            return str((int(cursor) if cursor.isdigit() else 0) + 1)
        return None

    @staticmethod
    def _invalid() -> ContentIntegrationError:
        return ContentIntegrationError(
            IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider returned invalid data"
        )
