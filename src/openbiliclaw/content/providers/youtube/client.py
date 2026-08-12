"""Typed YouTube client and concrete anonymous InnerTube transport."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlencode

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.infrastructure.http.clients import HttpClientFactory

from .models import YouTubeChannel, YouTubePage, YouTubeVideo

_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DEFAULT_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
_CONTEXT: dict[str, object] = {
    "client": {"clientName": "WEB", "clientVersion": "2.20240101.00.00", "hl": "en"}
}


class YouTubeTransport(Protocol):
    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes: ...


def _mapping(value: JsonValue | None) -> dict[str, JsonValue] | None:
    return value if isinstance(value, dict) else None


def _text(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value.strip()
    mapping = _mapping(value)
    if mapping is None:
        return ""
    simple = mapping.get("simpleText")
    if isinstance(simple, str):
        return simple.strip()
    runs = mapping.get("runs")
    if not isinstance(runs, list):
        return ""
    return "".join(
        str(run.get("text") or "") for value in runs if (run := _mapping(value)) is not None
    ).strip()


def _count(value: JsonValue | None) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    match = re.search(r"([\d.]+)\s*([KMB]?)", _text(value).replace(",", ""), re.IGNORECASE)
    if match is None:
        return 0
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return max(0, int(float(match.group(1)) * multiplier[match.group(2).upper()]))


def _duration(value: JsonValue | None) -> int:
    text = _text(value)
    if text.isdigit():
        return int(text)
    try:
        parts = [int(part) for part in text.split(":")]
    except ValueError:
        return 0
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def _published(value: JsonValue | None) -> datetime:
    text = _text(value)
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
    for pattern in ("%b %d, %Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
    return datetime(1970, 1, 1, tzinfo=UTC)


def _thumbnail(value: JsonValue | None) -> str | None:
    mapping = _mapping(value)
    thumbnails = mapping.get("thumbnails") if mapping is not None else None
    if not isinstance(thumbnails, list):
        return None
    for candidate in reversed(thumbnails):
        item = _mapping(candidate)
        url = item.get("url") if item is not None else None
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    return None


def _channel(renderer: dict[str, JsonValue]) -> YouTubeChannel | None:
    owner = _mapping(
        renderer.get("ownerText")
        or renderer.get("shortBylineText")
        or renderer.get("longBylineText")
    )
    name = _text(owner)
    runs = owner.get("runs") if owner is not None else None
    if not name or not isinstance(runs, list) or not runs:
        return None
    first = _mapping(runs[0])
    endpoint = _mapping(first.get("navigationEndpoint")) if first is not None else None
    browse = _mapping(endpoint.get("browseEndpoint")) if endpoint is not None else None
    channel_id = browse.get("browseId") if browse is not None else None
    if not isinstance(channel_id, str) or not channel_id:
        return None
    return YouTubeChannel(id=channel_id, name=name)


def _renderer_video(renderer: dict[str, JsonValue]) -> YouTubeVideo | None:
    video_id = renderer.get("videoId") or renderer.get("id")
    title = _text(renderer.get("title"))
    if not isinstance(video_id, str) or _VIDEO_ID.fullmatch(video_id) is None or not title:
        return None
    return YouTubeVideo(
        id=video_id,
        title=title,
        description=_text(renderer.get("descriptionSnippet") or renderer.get("description"))[
            :20_000
        ],
        channel=_channel(renderer),
        published_at=_published(renderer.get("publishedTimeText") or renderer.get("publishedAt")),
        duration_seconds=_duration(renderer.get("lengthText") or renderer.get("lengthSeconds")),
        view_count=_count(renderer.get("viewCountText") or renderer.get("viewCount")),
        thumbnail_url=_thumbnail(renderer.get("thumbnail")),
        availability="available",
    )


def _walk(value: JsonValue, videos: list[YouTubeVideo], cursors: list[str], limit: int) -> None:
    if len(videos) >= limit:
        return
    if isinstance(value, list):
        for child in value:
            _walk(child, videos, cursors, limit)
        return
    mapping = _mapping(value)
    if mapping is None:
        return
    renderer = _mapping(mapping.get("videoRenderer"))
    if renderer is not None:
        video = _renderer_video(renderer)
        if video is not None:
            videos.append(video)
    continuation = _mapping(mapping.get("continuationCommand"))
    token = continuation.get("token") if continuation is not None else None
    if isinstance(token, str) and token:
        cursors.append(token)
    for child in mapping.values():
        _walk(child, videos, cursors, limit)


def _player_video(payload: dict[str, JsonValue]) -> YouTubeVideo | None:
    details = _mapping(payload.get("videoDetails"))
    if details is None:
        return None
    microformat = _mapping(payload.get("microformat"))
    player = _mapping(microformat.get("playerMicroformatRenderer")) if microformat else None
    renderer: dict[str, JsonValue] = {
        "videoId": details.get("videoId"),
        "title": details.get("title"),
        "description": details.get("shortDescription"),
        "lengthSeconds": details.get("lengthSeconds"),
        "viewCount": details.get("viewCount"),
        "thumbnail": details.get("thumbnail"),
        "publishedAt": player.get("publishDate") if player else None,
    }
    video = _renderer_video(renderer)
    channel_id = details.get("channelId")
    author = details.get("author")
    if video is None or not isinstance(channel_id, str) or not isinstance(author, str):
        return video
    return video.model_copy(update={"channel": YouTubeChannel(id=channel_id, name=author)})


class HttpxYouTubeTransport:
    """Anonymous InnerTube boundary using scoped infrastructure HTTP clients."""

    _BASE = "https://www.youtube.com/youtubei/v1"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._factory = HttpClientFactory()
        self._transport = transport

    @property
    def open_client_count(self) -> int:
        return self._factory.open_client_count

    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes:
        path, body = self._request(operation, argument, cursor)
        url = f"{self._BASE}/{path}?{urlencode({'key': _DEFAULT_KEY})}"
        async with self._factory.client(transport=self._transport) as client:
            try:
                response = await self._factory.request(
                    client,
                    "POST",
                    url,
                    headers={"content-type": "application/json"},
                    content=json.dumps(body).encode(),
                    idempotency_key="youtube-read",
                )
            except httpx.TransportError as exc:
                raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
        if response.status_code == 429:
            raise _error(IntegrationErrorCode.RATE_LIMITED)
        if response.status_code in {401, 403}:
            raise _error(IntegrationErrorCode.ACCESS_DENIED)
        if response.status_code >= 400:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE)
        try:
            parsed = _JSON.validate_json(response.content)
            mapping = _mapping(parsed)
            if mapping is None:
                raise ValueError("response is not an object")
            videos: list[YouTubeVideo] = []
            cursors: list[str] = []
            if operation == "fetch":
                video = _player_video(mapping)
                if video is not None:
                    videos.append(video)
            else:
                _walk(mapping, videos, cursors, min(50, max(1, limit)))
            page = YouTubePage(
                items=tuple(videos[: min(50, max(1, limit))]),
                next_cursor=cursors[0] if cursors else None,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise _error(IntegrationErrorCode.PROVIDER_UNAVAILABLE) from exc
        return page.model_dump_json().encode()

    @staticmethod
    def _request(operation: str, argument: str, cursor: str) -> tuple[str, dict[str, object]]:
        body: dict[str, object] = {"context": _CONTEXT}
        if cursor != "0":
            body["continuation"] = cursor
            return ("search" if operation == "search" else "browse"), body
        if operation == "search":
            body["query"] = argument
            return "search", body
        if operation == "fetch":
            body["videoId"] = argument
            return "player", body
        body["browseId"] = "FEtrending" if operation == "feed" else argument
        return "browse", body


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
