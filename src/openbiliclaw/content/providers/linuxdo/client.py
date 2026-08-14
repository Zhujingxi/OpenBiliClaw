"""Typed Linux.do client and concrete anonymous Discourse JSON transport."""

from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlencode

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from openbiliclaw.access.models import AccessHandle, CredentialAccessHandle
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.infrastructure.http.clients import HttpClientFactory

from .models import LinuxDoItem, LinuxDoPage

_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_USER_AGENT = "OpenBiliClaw/0.1 (+https://github.com/jingxi/OpenBiliClaw)"


class LinuxDoTransport(Protocol):
    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes: ...
    async def fetch(self, content_id: str, credential: str | None) -> bytes: ...


class CredentialResolver(Protocol):
    async def __call__(self, handle: CredentialAccessHandle) -> str: ...


def _mapping(value: JsonValue | None) -> dict[str, JsonValue] | None:
    return value if isinstance(value, dict) else None


def _integer(value: JsonValue | None) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _text(value: JsonValue | None) -> str:
    return value if isinstance(value, str) else ""


def _timestamp(value: JsonValue | None) -> int:
    if not isinstance(value, str):
        return 0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0, int(parsed.timestamp()))
    except (OverflowError, ValueError):
        return 0


class _HTMLTextExtractor(HTMLParser):
    """Small allowlist-free text extractor for Discourse's cooked HTML."""

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
    return " ".join("".join(parser.parts).split())[:50_000]


def _topic(row: dict[str, JsonValue], posts: dict[int, dict[str, JsonValue]]) -> LinuxDoItem:
    topic_id = _integer(row.get("id"))
    post = posts.get(topic_id, {})
    return LinuxDoItem(
        id=str(topic_id),
        title=_text(row.get("title")),
        body=_html_text(post.get("blurb") or post.get("cooked")),
        author=_text(post.get("username")),
        url=f"https://linux.do/t/topic/{topic_id}",
        published_at=_timestamp(row.get("created_at")),
        deleted=False,
    )


def _fetched_topic(row: dict[str, JsonValue], content_id: str) -> LinuxDoItem:
    stream = _mapping(row.get("post_stream")) or {}
    raw_posts = stream.get("posts")
    posts = raw_posts if isinstance(raw_posts, list) else []
    first = _mapping(posts[0]) if posts else None
    if first is None:
        raise ValueError("topic has no first post")
    topic_id = _integer(row.get("id"))
    if topic_id <= 0 or str(topic_id) != content_id:
        raise ValueError("topic identity mismatch")
    return LinuxDoItem(
        id=str(topic_id),
        title=_text(row.get("title")),
        body=_html_text(first.get("cooked")),
        author=_text(first.get("username")),
        url=f"https://linux.do/t/topic/{topic_id}",
        published_at=_timestamp(first.get("created_at") or row.get("created_at")),
        deleted=False,
    )


class HttpxLinuxDoTransport:
    """Anonymous read-only boundary for Linux.do's public Discourse JSON API."""

    _BASE = "https://linux.do"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._factory = HttpClientFactory()
        self._transport = transport

    @property
    def open_client_count(self) -> int:
        return self._factory.open_client_count

    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes:
        page_number = max(0, int(cursor or "0"))
        parsed = await self._get(
            "/search.json",
            urlencode({"q": text, "page": page_number}),
            credential,
        )
        try:
            envelope = _mapping(parsed)
            if envelope is None:
                raise ValueError("invalid search response")
            raw_topics = envelope.get("topics")
            raw_posts = envelope.get("posts")
            if not isinstance(raw_topics, list) or not isinstance(raw_posts, list):
                raise ValueError("invalid search response")
            posts = {
                topic_id: post
                for value in raw_posts
                if (post := _mapping(value)) is not None
                and (topic_id := _integer(post.get("topic_id"))) > 0
            }
            page_limit = min(50, max(1, limit))
            items = tuple(
                _topic(topic, posts)
                for value in raw_topics[:page_limit]
                if (topic := _mapping(value)) is not None
            )
            grouped = _mapping(envelope.get("grouped_search_result")) or {}
            next_cursor = str(page_number + 1) if grouped.get("more_posts") is True else None
            page = LinuxDoPage(items=items, next_cursor=next_cursor)
        except (ValidationError, ValueError, TypeError) as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
        return page.model_dump_json().encode()

    async def fetch(self, content_id: str, credential: str | None) -> bytes:
        parsed = await self._get(f"/t/{content_id}.json", "", credential)
        try:
            row = _mapping(parsed)
            if row is None:
                raise ValueError("invalid topic response")
            item = _fetched_topic(row, content_id)
        except (ValidationError, ValueError, TypeError) as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
        return item.model_dump_json().encode()

    async def _get(self, path: str, query: str, credential: str | None) -> JsonValue:
        headers = {"accept": "application/json", "user-agent": _USER_AGENT}
        if credential:
            headers["cookie"] = credential
        url = f"{self._BASE}{path}" + (f"?{query}" if query else "")
        async with self._factory.client(transport=self._transport) as client:
            try:
                response = await self._factory.request(client, "GET", url, headers=headers)
            except httpx.TransportError as exc:
                raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
        _check_status(response)
        try:
            return _JSON.validate_json(response.content)
        except ValidationError as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc


class LinuxDoClient:
    def __init__(
        self, transport: LinuxDoTransport, resolver: CredentialResolver | None = None
    ) -> None:
        self._transport = transport
        self._resolver = resolver

    def __repr__(self) -> str:
        return "LinuxDoClient(credentials=<opaque>)"

    async def search(
        self, text: str, cursor: str | None, limit: int, access: AccessHandle
    ) -> LinuxDoPage:
        raw = await self._transport.search(text, cursor, limit, await self._credential(access))
        try:
            return LinuxDoPage.model_validate_json(raw)
        except ValidationError as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc

    async def fetch(self, content_id: str, access: AccessHandle) -> LinuxDoItem:
        raw = await self._transport.fetch(content_id, await self._credential(access))
        try:
            return LinuxDoItem.model_validate_json(raw)
        except ValidationError as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc

    async def _credential(self, access: AccessHandle) -> str | None:
        if isinstance(access, CredentialAccessHandle) and self._resolver is not None:
            return await self._resolver(access)
        return None


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
