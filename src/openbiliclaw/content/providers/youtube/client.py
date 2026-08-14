"""Typed YouTube client backed by yt-dlp's maintained extractors."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Protocol, cast
from urllib.error import URLError
from urllib.parse import quote

import anyio
from pydantic import ValidationError
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode

from .models import YouTubeChannel, YouTubePage, YouTubeVideo

if TYPE_CHECKING:
    from yt_dlp import _Params

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]+$")


class YouTubeTransport(Protocol):
    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes: ...


class YoutubeDLSession(Protocol):
    def __enter__(self) -> YoutubeDLSession: ...

    def __exit__(self, *args: object) -> object: ...

    def extract_info(self, target: str, *, download: bool) -> object: ...


YoutubeDLFactory = Callable[[Mapping[str, object]], YoutubeDLSession]


def _youtube_dl(options: Mapping[str, object]) -> YoutubeDLSession:
    return YoutubeDL(cast("_Params", dict(options)))


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _integer(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(float(value)))
        except ValueError:
            return 0
    return 0


def _published(entry: Mapping[str, object]) -> datetime:
    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        try:
            return datetime.fromtimestamp(timestamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            pass
    upload_date = _string(entry.get("upload_date"))
    try:
        return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=UTC)


def _thumbnail(entry: Mapping[str, object]) -> str | None:
    direct = _string(entry.get("thumbnail"))
    if direct.startswith(("http://", "https://")):
        return direct
    values = entry.get("thumbnails")
    if not isinstance(values, list):
        return None
    for value in reversed(values):
        item = _mapping(value)
        url = _string(item.get("url")) if item is not None else ""
        if url.startswith(("http://", "https://")):
            return url
    return None


def _channel(entry: Mapping[str, object]) -> YouTubeChannel | None:
    channel_id = _string(entry.get("channel_id") or entry.get("uploader_id"))
    name = _string(entry.get("channel") or entry.get("uploader"))
    if not channel_id or not name:
        return None
    return YouTubeChannel(id=channel_id[:128], name=name[:300])


def _video(value: object) -> YouTubeVideo | None:
    entry = _mapping(value)
    if entry is None:
        return None
    video_id = _string(entry.get("id"))
    title = _string(entry.get("title"))
    if _VIDEO_ID.fullmatch(video_id) is None or not title:
        return None
    description = _string(entry.get("description"))[:20_000]
    return YouTubeVideo(
        id=video_id,
        title=title[:500],
        description=description,
        channel=_channel(entry),
        published_at=_published(entry),
        duration_seconds=_integer(entry.get("duration")),
        view_count=_integer(entry.get("view_count")),
        thumbnail_url=_thumbnail(entry),
        availability="available",
    )


def _classify(error: BaseException, operation: str) -> IntegrationErrorCode:
    text = str(error).lower()
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return IntegrationErrorCode.RATE_LIMITED
    if "403" in text or "forbidden" in text or "sign in" in text or "age-restricted" in text:
        return IntegrationErrorCode.ACCESS_DENIED
    if operation == "fetch" and any(
        marker in text for marker in ("unavailable", "not available", "private", "removed")
    ):
        return IntegrationErrorCode.INVALID_CONTENT_REF
    if isinstance(error, (OSError, URLError)) or any(
        marker in text
        for marker in ("network", "connection", "timed out", "timeout", "dns", "temporary failure")
    ):
        return IntegrationErrorCode.NETWORK_UNAVAILABLE
    return IntegrationErrorCode.PROVIDER_UNAVAILABLE


class YtDlpYouTubeTransport:
    """Run yt-dlp's synchronous extractors off the event loop."""

    def __init__(self, factory: YoutubeDLFactory = _youtube_dl) -> None:
        self._factory = factory

    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes:
        capped = min(50, max(1, limit))
        if cursor != "0":
            return YouTubePage(items=(), next_cursor=None).model_dump_json().encode()
        target, flat = self._target(operation, argument, capped)
        options: dict[str, object] = {
            "extract_flat": flat,
            "noplaylist": operation == "fetch",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "playlistend": capped,
        }
        try:
            payload = await anyio.to_thread.run_sync(
                partial(self._extract, options, target), abandon_on_cancel=True
            )
            items = self._items(payload, operation, capped)
            return YouTubePage(items=items, next_cursor=None).model_dump_json().encode()
        except ContentIntegrationError:
            raise
        except (DownloadError, OSError, URLError) as exc:
            raise _error(_classify(exc, operation)) from exc
        except (TypeError, ValidationError, ValueError) as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc

    def _extract(self, options: Mapping[str, object], target: str) -> Mapping[str, object]:
        with self._factory(options) as session:
            payload = _mapping(session.extract_info(target, download=False))
        if payload is None:
            raise ValueError("yt-dlp response is not an object")
        return payload

    @staticmethod
    def _target(operation: str, argument: str, limit: int) -> tuple[str, bool]:
        if operation == "search":
            if not argument.strip():
                raise _error(IntegrationErrorCode.INVALID_CONTENT_REF)
            return f"ytsearch{limit}:{argument}", True
        if operation == "fetch":
            if _VIDEO_ID.fullmatch(argument) is None:
                raise _error(IntegrationErrorCode.INVALID_CONTENT_REF)
            return f"https://www.youtube.com/watch?v={argument}", False
        if operation == "creator":
            if _CHANNEL_ID.fullmatch(argument):
                return f"https://www.youtube.com/channel/{argument}/videos", True
            if argument.startswith("@") and len(argument) > 1:
                return f"https://www.youtube.com/{quote(argument, safe='@_-')}/videos", True
            raise _error(IntegrationErrorCode.INVALID_CONTENT_REF)
        raise _error(IntegrationErrorCode.UNAVAILABLE_CAPABILITY)

    @staticmethod
    def _items(
        payload: Mapping[str, object], operation: str, limit: int
    ) -> tuple[YouTubeVideo, ...]:
        raw_items = payload.get("entries") if operation != "fetch" else [payload]
        if not isinstance(raw_items, list):
            raw_items = []
        items: list[YouTubeVideo] = []
        seen: set[str] = set()
        for value in raw_items:
            item = _video(value)
            if item is None or item.id in seen:
                continue
            seen.add(item.id)
            items.append(item)
            if len(items) >= limit:
                break
        return tuple(items)


class YouTubeClient:
    def __init__(self, transport: YouTubeTransport) -> None:
        self._transport = transport

    async def page(self, operation: str, argument: str, cursor: str, limit: int) -> YouTubePage:
        try:
            raw = await self._transport(operation, argument, cursor, limit)
            return YouTubePage.model_validate_json(raw)
        except ContentIntegrationError:
            raise
        except (ValidationError, ValueError) as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc


def _error(code: IntegrationErrorCode) -> ContentIntegrationError:
    message = (
        "provider rate limited"
        if code is IntegrationErrorCode.RATE_LIMITED
        else "provider request failed"
    )
    return ContentIntegrationError(code, message)
