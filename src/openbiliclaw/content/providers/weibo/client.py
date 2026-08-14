"""Typed Weibo client and anonymous visitor-backed HTTP transport."""

from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlencode

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.infrastructure.http.clients import HttpClientFactory

if TYPE_CHECKING:
    from openbiliclaw.access.models import CredentialAccessHandle

from .models import WeiboItem, WeiboPage

_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
_ENTRY_URL = "https://visitor.passport.weibo.cn/visitor/visitor"
_GENERATE_URL = "https://visitor.passport.weibo.cn/visitor/genvisitor2"
_EXCHANGE_URL = "https://passport.weibo.com/visitor/visitor"
_MOBILE_URL = "https://m.weibo.cn/api/container/getIndex"
_REQUEST_ID = re.compile(r"\bvar\s+request_id\s*=\s*([\"'])(?P<value>.+?)\1\s*;")
_RETURN_URL = re.compile(r"\bvar\s+return_url\s*=\s*([\"'])(?P<value>.+?)\1\s*;")
_GENERATE_CALL = re.compile(
    r"/visitor/genvisitor2.*?\bcb=(?P<callback>[A-Za-z_$][A-Za-z0-9_$]{0,127})"
    r"&ver=(?P<version>[A-Za-z0-9_.-]{1,32})&request_id\b",
    re.DOTALL,
)


class WeiboTransport(Protocol):
    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes: ...


def _mapping(value: JsonValue | None) -> dict[str, JsonValue] | None:
    return value if isinstance(value, dict) else None


def _text(value: JsonValue | None) -> str:
    return value if isinstance(value, str) else ""


class _HTMLTextExtractor(HTMLParser):
    _IGNORED = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self._IGNORED:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._IGNORED and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _html_text(value: JsonValue | None) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(_text(value))
    parser.close()
    normalized = " ".join("".join(parser.parts).split())
    return re.sub(r"(?:\s|\.{3}|…)*全文$", "", normalized).strip()[:50_000]


def _timestamp(value: JsonValue | None) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    if not isinstance(value, str):
        return 0
    for pattern in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return max(0, int(datetime.strptime(value, pattern).timestamp()))
        except (OverflowError, ValueError):
            pass
    return 0


def _mblog(row: dict[str, JsonValue]) -> WeiboItem:
    content_id = _text(row.get("idstr")) or _text(row.get("id"))
    if not content_id:
        numeric_id = row.get("id")
        content_id = str(numeric_id) if isinstance(numeric_id, int) else ""
    bid = _text(row.get("bid"))
    if not bid:
        raise ValueError("mblog omitted canonical bid")
    body = _html_text(row.get("text"))
    user = _mapping(row.get("user")) or {}
    title = body.split("\n", 1)[0][:100].strip()
    if len(body) > len(title) and len(title) >= 100:
        title = f"{title.rstrip()}…"
    return WeiboItem(
        id=content_id,
        title=title,
        body=body,
        author=_text(user.get("screen_name")) or _text(user.get("name")),
        url=f"https://weibo.com/status/{bid}",
        published_at=_timestamp(row.get("created_at")),
        deleted=False,
    )


def _mblogs(cards: JsonValue | None, limit: int) -> tuple[WeiboItem, ...]:
    if not isinstance(cards, list):
        raise ValueError("invalid cards")
    rows: list[WeiboItem] = []
    stack: list[JsonValue] = list(reversed(cards))
    while stack and len(rows) < limit:
        value = stack.pop()
        card = _mapping(value)
        if card is None:
            continue
        row = _mapping(card.get("mblog"))
        if row is not None:
            try:
                rows.append(_mblog(row))
            except (ValidationError, ValueError):
                continue
        else:
            group = card.get("card_group")
            if isinstance(group, list):
                stack.extend(reversed(group))
    return tuple(rows)


def _jsonp(value: str, callback: str) -> dict[str, JsonValue]:
    escaped = re.escape(callback)
    match = re.fullmatch(
        rf"(?:window\.{escaped}\s*&&\s*)?{escaped}\s*\(\s*(?P<payload>\{{.*\}})\s*\)\s*;?\s*",
        value.strip(),
        re.DOTALL,
    )
    if match is None:
        raise ValueError("invalid visitor JSONP")
    parsed = json.loads(match.group("payload"))
    if not isinstance(parsed, dict):
        raise ValueError("invalid visitor payload")
    return parsed


def _js_string(value: str) -> str:
    try:
        decoded = json.loads(f'"{value}"')
    except json.JSONDecodeError as exc:
        raise ValueError("invalid visitor string") from exc
    if not isinstance(decoded, str):
        raise ValueError("invalid visitor string")
    return html.unescape(decoded)


def _safe_cookie(value: JsonValue | None) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 4096
        or ";" in candidate
        or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
    ):
        return ""
    return candidate


class HttpxWeiboTransport:
    """Anonymous Weibo boundary that keeps only a generated visitor SUB in memory."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._factory = HttpClientFactory()
        self._transport = transport
        self._visitor_sub: str | None = None
        self._visitor_lock = asyncio.Lock()

    @property
    def open_client_count(self) -> int:
        return self._factory.open_client_count

    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes:
        del credential  # Anonymous-only by design; never replay a user cookie.
        page_number = max(1, int(cursor or "1"))
        page_limit = min(50, max(1, limit))
        # 100103type=64 (hot sort) is the anonymous keyword-search container;
        # type=1 (综合) returns ok=0 "这里还没有内容" to visitor cookies (2026-08).
        query = urlencode({"containerid": f"100103type=64&q={text.strip()}", "page": page_number})
        url = f"{_MOBILE_URL}?{query}"
        # Upstream also soft-empties visitor cookies: ok=1 with total=0/no
        # mblog cards for queries that have results (2026-08 probe). A fresh
        # visitor cookie re-rolls the dice; bounded retries, then accept the
        # empty page (indistinguishable from a genuine no-results query).
        for attempt in range(6):
            page = await self._search_once(url, page_number, page_limit)
            if page.items or attempt == 5:
                return page.model_dump_json().encode()
            await asyncio.sleep(1.0)
            await self._refresh_visitor(url)
        raise AssertionError("unreachable")

    async def _search_once(self, url: str, page_number: int, page_limit: int) -> WeiboPage:
        parsed = await self._mobile_json(url)
        try:
            envelope = _mapping(parsed)
            data = _mapping(envelope.get("data")) if envelope is not None else None
            if envelope is None or envelope.get("ok") not in (1, "1") or data is None:
                raise ValueError("invalid search response")
            cards = data.get("cards")
            items = _mblogs(cards, page_limit)
            info = _mapping(data.get("cardlistInfo")) or {}
            if isinstance(cards, list) and cards and not items and info.get("total") != 0:
                raise ValueError("search page contained no valid mblogs")
            raw_next = info.get("page")
            next_cursor = (
                str(raw_next)
                if isinstance(raw_next, int) and raw_next > page_number and items
                else None
            )
            return WeiboPage(items=items, next_cursor=next_cursor)
        except (ValidationError, ValueError, TypeError) as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc

    async def _mobile_json(self, url: str) -> JsonValue:
        await self._ensure_visitor(url)
        # ok=0 "这里还没有内容" is an upstream soft-block against the visitor
        # cookie (~50% per cookie, 2026-08 probe); a fresh cookie re-rolls it.
        for attempt in range(6):
            response = await self._request(
                "GET",
                url,
                headers={"accept": "application/json", "cookie": f"SUB={self._visitor_sub}"},
            )
            if response.status_code in {401, 403}:
                if attempt < 5:
                    await asyncio.sleep(1.0)
                    await self._refresh_visitor(url)
                    continue
                raise _error(IntegrationErrorCode.ACCESS_DENIED)
            _check_status(response)
            try:
                parsed = _JSON.validate_json(response.content)
            except ValidationError as exc:
                raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
            envelope = _mapping(parsed)
            if envelope is not None and envelope.get("ok") in (0, "0"):
                if attempt < 5:
                    await asyncio.sleep(1.0)
                    await self._refresh_visitor(url)
                    continue
                raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE)
            return parsed
        raise _error(IntegrationErrorCode.ACCESS_DENIED)

    async def _ensure_visitor(self, return_url: str) -> None:
        if self._visitor_sub is not None:
            return
        async with self._visitor_lock:
            if self._visitor_sub is None:
                self._visitor_sub = await self._bootstrap(return_url)

    async def _refresh_visitor(self, return_url: str) -> None:
        async with self._visitor_lock:
            self._visitor_sub = await self._bootstrap(return_url)

    async def _bootstrap(self, return_url: str) -> str:
        entry_query = urlencode(
            {
                "entry": "sinawap",
                "a": "enter",
                "url": return_url,
                "domain": ".weibo.cn",
                "ua": "php-sso_sdk_client-0.6.36",
            }
        )
        entry_url = f"{_ENTRY_URL}?{entry_query}"
        entry = await self._request("GET", entry_url, headers={"accept": "text/html"})
        _check_status(entry)
        request_match = _REQUEST_ID.search(entry.text)
        return_match = _RETURN_URL.search(entry.text)
        call_match = _GENERATE_CALL.search(entry.text)
        if request_match is None or return_match is None or call_match is None:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE)
        try:
            request_id = _js_string(request_match.group("value"))
            parsed_return_url = _js_string(return_match.group("value"))
        except ValueError as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
        if parsed_return_url != return_url or not request_id:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE)
        callback = call_match.group("callback")
        form = urlencode(
            {
                "cb": callback,
                "ver": call_match.group("version"),
                "request_id": request_id,
                "tid": "",
                "from": "weibo",
                "webdriver": "false",
                "return_url": parsed_return_url,
            }
        ).encode()
        generated = await self._request(
            "POST",
            _GENERATE_URL,
            headers={
                "accept": "application/javascript",
                "content-type": "application/x-www-form-urlencoded",
            },
            content=form,
        )
        _check_status(generated)
        try:
            payload = _jsonp(generated.text, callback)
            data = _mapping(payload.get("data")) or {}
            sub = _safe_cookie(data.get("sub"))
            if sub:
                return sub
            tid = _text(data.get("tid")) or _text(data.get("new_tid"))
            confidence = _text(data.get("confidence"))
            if not tid:
                raise ValueError("visitor service omitted identity")
            exchange_query = urlencode(
                {
                    "a": "incarnate",
                    "t": tid,
                    "w": 2,
                    "c": confidence,
                    "gc": "",
                    "cb": callback,
                    "from": "weibo",
                }
            )
            exchange_url = f"{_EXCHANGE_URL}?{exchange_query}"
            exchanged = await self._request("GET", exchange_url)
            _check_status(exchanged)
            sub = _safe_cookie(exchanged.cookies.get("SUB"))
            if not sub:
                exchange_payload = _jsonp(exchanged.text, callback)
                exchange_data = _mapping(exchange_payload.get("data")) or {}
                sub = _safe_cookie(exchange_data.get("sub"))
            if not sub:
                raise ValueError("visitor exchange omitted cookie")
            return sub
        except (json.JSONDecodeError, ValueError) as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        safe_headers = {"user-agent": _USER_AGENT, **(headers or {})}
        safe_headers.pop("authorization", None)
        async with self._factory.client(transport=self._transport) as client:
            try:
                return await self._factory.request(
                    client,
                    method,
                    url,
                    headers=safe_headers,
                    content=content,
                    idempotency_key="weibo-anonymous" if method == "POST" else None,
                )
            except httpx.TransportError as exc:
                raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc


class WeiboClient:
    def __init__(self, transport: WeiboTransport) -> None:
        self._transport = transport

    def __repr__(self) -> str:
        return "WeiboClient(credentials=<anonymous>)"

    async def search(
        self, text: str, cursor: str | None, limit: int, access: CredentialAccessHandle | None
    ) -> WeiboPage:
        raw = await self._transport.search(text, cursor, limit, None)
        try:
            return WeiboPage.model_validate_json(raw)
        except ValidationError as exc:
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
