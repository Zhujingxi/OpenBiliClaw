"""YouTube scraper client retained behind the vNext connector.

Wraps scrapetube (search + channel) and YouTube InnerTube API (trending)
behind a single async interface, with anonymous yt-dlp flat-playlist
extraction as the fallback layer where yt-dlp has a working entry point
(yt-dlp tracks YouTube markup changes fastest, so a broken primary
degrades instead of silently starving the source). All blocking calls run
in the default thread executor so they don't stall the event loop.

Supports three discovery modes:
  - search_videos       — keyword search via scrapetube → yt-dlp ytsearch
  - get_trending        — InnerTube FEtrending → public topic pages
  - get_channel_videos  — channel uploads via scrapetube → yt-dlp

Field-name notes (scrapetube returns YouTube's internal renderer dicts):
  title         → {"runs": [{"text": "..."}]}  or  {"simpleText": "..."}
  ownerText     → {"runs": [{"text": "channel name"}]}
  viewCountText → {"simpleText": "1,234,567 views"}
  lengthText    → {"simpleText": "12:34"}
  thumbnail     → {"thumbnails": [{"url": "...", "width": N, "height": N}]}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_DEFAULT_REGION = "US"
_TRENDING_TOPIC_PATHS: tuple[str, ...] = (
    "gaming",
    "sports",
    "news",
    "podcasts",
    "live",
)

# InnerTube client config for anonymous web requests
_INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
_INNERTUBE_CLIENT_VERSION = "2.20240101.00.00"
_INNERTUBE_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": _INNERTUBE_CLIENT_VERSION,
        "hl": "en",
    }
}

# Shared yt-dlp options for anonymous flat-playlist metadata extraction
# (search / channel / trending fallbacks — never downloads media).
_YTDLP_FLAT_OPTIONS: dict[str, Any] = {
    "quiet": True,
    "extract_flat": True,
    "skip_download": True,
    "noplaylist": False,
    "ignoreerrors": True,
    "socket_timeout": 20,
}


def _ytdlp_options(**extra: Any) -> dict[str, Any]:
    """Base flat yt-dlp options plus the overseas outbound proxy when set.

    YouTube is an overseas source, so it honors ``[network].proxy``. yt-dlp's
    native ``proxy`` option routes its HTTP through it; omitting the key keeps
    yt-dlp's default (env-inheriting) behavior — zero drift when unset.
    """
    from openbiliclaw.network import outbound_ytdlp_proxy

    options: dict[str, Any] = {**_YTDLP_FLAT_OPTIONS, **extra}
    proxy = outbound_ytdlp_proxy()
    if proxy is not None:
        options["proxy"] = proxy
    return options


@dataclass(frozen=True)
class InnerTubeConfig:
    api_key: str = _INNERTUBE_KEY
    client_version: str = _INNERTUBE_CLIENT_VERSION
    client_name: str = "WEB"
    client_name_header: str = "1"


# ---------------------------------------------------------------------------
# Blocking helpers (run in executor)
# ---------------------------------------------------------------------------


def _scrapetube_search(query: str, limit: int) -> list[dict[str, Any]]:
    try:
        import scrapetube  # type: ignore[import-untyped]

        from openbiliclaw.network import outbound_requests_proxies

        results = [
            dict(v)
            for v in scrapetube.get_search(
                query,
                results_type="video",
                limit=limit,
                proxies=outbound_requests_proxies(),
            )
        ]
        if results:
            return results
        logger.info("scrapetube.search(%r) returned 0 items; falling back to yt-dlp", query)
    except Exception as exc:
        logger.warning("scrapetube.search(%r) failed (%s); falling back to yt-dlp", query, exc)
    return _ytdlp_search(query, limit)


def _ytdlp_search(query: str, limit: int) -> list[dict[str, Any]]:
    """Search fallback via yt-dlp's ``ytsearchN:`` pseudo-URL.

    yt-dlp tracks YouTube markup changes far faster than scrapetube, so a
    broken/blocked scrapetube search degrades to a slower-but-working path
    instead of silently starving the YouTube candidate supply.
    """
    if not query.strip():
        return []
    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-untyped]

        with YoutubeDL(_ytdlp_options()) as ydl:
            info = ydl.extract_info(f"ytsearch{max(1, limit)}:{query}", download=False)
        return _ytdlp_entries(info, limit)
    except Exception as exc:
        logger.warning("yt-dlp.search(%r) failed: %s", query, exc)
        return []


def _scrapetube_channel(channel_id: str, limit: int) -> list[dict[str, Any]]:
    try:
        import scrapetube

        from openbiliclaw.network import outbound_requests_proxies

        proxies = outbound_requests_proxies()
        if channel_id.startswith("@") or channel_id.startswith("UC"):
            results = [
                dict(v)
                for v in scrapetube.get_channel(
                    channel_url=None,
                    channel_id=channel_id,
                    limit=limit,
                    proxies=proxies,
                )
            ]
        else:
            results = [
                dict(v)
                for v in scrapetube.get_channel(
                    channel_url=channel_id,
                    limit=limit,
                    proxies=proxies,
                )
            ]
        if results:
            return results
    except Exception as exc:
        logger.warning("scrapetube.channel(%r) failed: %s", channel_id, exc)
    return _ytdlp_channel(channel_id, limit)


def _ytdlp_entries(info: Any, limit: int) -> list[dict[str, Any]]:
    """Map a yt-dlp flat-playlist info dict to video dicts for normalize_yt_video."""
    if not isinstance(info, dict):
        return []
    entries = info.get("entries")
    if not isinstance(entries, list):
        return []
    results: list[dict[str, Any]] = []
    for entry in entries[:limit]:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        if not item.get("videoId") and item.get("id"):
            item["videoId"] = item["id"]
        if not item.get("channel") and info.get("channel"):
            item["channel"] = info.get("channel")
        results.append(item)
    return results


def _ytdlp_channel(channel_ref: str, limit: int) -> list[dict[str, Any]]:
    """Fetch channel uploads with yt-dlp when scrapetube cannot resolve handles."""
    url = _channel_uploads_url(channel_ref)
    if not url:
        return []
    try:
        from yt_dlp import YoutubeDL

        with YoutubeDL(_ytdlp_options(playlistend=limit)) as ydl:
            info = ydl.extract_info(url, download=False)
        return _ytdlp_entries(info, limit)
    except Exception as exc:
        logger.warning("yt-dlp.channel(%r) failed: %s", channel_ref, exc)
        return []


def _channel_uploads_url(channel_ref: str) -> str:
    ref = channel_ref.strip()
    if not ref:
        return ""
    if ref.startswith("http://") or ref.startswith("https://"):
        base = ref.rstrip("/")
        return base if base.endswith("/videos") else f"{base}/videos"
    if ref.startswith("@"):
        return f"https://www.youtube.com/{ref}/videos"
    if ref.startswith("UC"):
        return f"https://www.youtube.com/channel/{ref}/videos"
    return ""


def _innertube_trending(region_code: str, limit: int) -> list[dict[str, Any]]:
    """Fetch YouTube trending via the InnerTube browse API (no API key needed).

    Uses the FEtrending browseId when YouTube still exposes it. If that
    endpoint is unavailable, falls back to public YouTube topic pages that
    still ship video renderers in ytInitialData. (yt-dlp is deliberately NOT
    a layer here: /feed/trending was removed by YouTube — verified 2026-07 to
    redirect to the home page — and yt-dlp's flat extraction gets nothing out
    of the shelf-based topic/browse surfaces.)
    Returns a flat list of video dicts ready for normalize_yt_video().
    """
    results = _innertube_trending_feed(region_code, limit)
    if results:
        return results
    return _topic_page_trending(region_code, limit)


def _innertube_trending_feed(region_code: str, limit: int) -> list[dict[str, Any]]:
    """Fetch the legacy FEtrending InnerTube browse feed."""
    try:
        config = _fetch_innertube_config(region_code)
        payload = json.dumps(
            {
                "browseId": "FEtrending",
                "context": {
                    **_INNERTUBE_CONTEXT,
                    "client": {
                        **_INNERTUBE_CONTEXT["client"],
                        "clientName": config.client_name,
                        "clientVersion": config.client_version,
                        "gl": region_code,
                    },
                },
            },
            ensure_ascii=False,
        ).encode()

        url = f"https://www.youtube.com/youtubei/v1/browse?key={config.api_key}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "X-YouTube-Client-Name": config.client_name_header,
            "X-YouTube-Client-Version": config.client_version,
        }
        from openbiliclaw.network import outbound_httpx_kwargs

        with httpx.Client(timeout=15, **outbound_httpx_kwargs()) as client:
            response = client.post(url, content=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        return list(_extract_innertube_videos(data, limit=limit))
    except Exception as exc:
        logger.warning("InnerTube trending(%s) failed: %s", region_code, exc)
        return []


def _topic_page_trending(
    region_code: str,
    limit: int,
    *,
    fetch_html: Callable[[str], str] | None = None,
    topic_paths: tuple[str, ...] = _TRENDING_TOPIC_PATHS,
) -> list[dict[str, Any]]:
    """Fallback trending supply from public YouTube topic pages."""
    fetch = fetch_html or _fetch_youtube_html
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for path in topic_paths:
        if len(results) >= limit:
            break
        url = f"https://www.youtube.com/{path}?gl={region_code}&persist_gl=1"
        try:
            html = fetch(url)
        except Exception as exc:
            logger.warning("YouTube topic page %s failed: %s", path, exc)
            continue
        for item in _extract_yt_initial_data_videos(html, limit=limit):
            video_id = str(item.get("videoId") or item.get("id") or "").strip()
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            results.append(item)
            if len(results) >= limit:
                break
    if results:
        logger.info("YouTube topic-page trending fallback returned %d videos", len(results))
    return results


def _fetch_youtube_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": "CONSENT=YES+1",
    }
    from openbiliclaw.network import outbound_httpx_kwargs

    with httpx.Client(timeout=20, **outbound_httpx_kwargs()) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return response.text


def _extract_yt_initial_data_videos(html: str, limit: int) -> list[dict[str, Any]]:
    data = _extract_yt_initial_data(html)
    if data is None:
        return []
    return _extract_innertube_videos(data, limit=limit)


def _extract_yt_initial_data(html: str) -> dict[str, Any] | None:
    for marker in (
        "var ytInitialData",
        'window["ytInitialData"]',
        "window['ytInitialData']",
    ):
        start = html.find(marker)
        if start < 0:
            continue
        parsed = _extract_json_object_after(html, start)
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_json_object_after(text: str, start: int) -> object | None:
    object_start = text.find("{", start)
    if object_start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(object_start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                parsed: object = json.loads(text[object_start : index + 1])
                return parsed
    return None


def _fetch_innertube_config(region_code: str) -> InnerTubeConfig:
    """Read the current web client config from YouTube's trending page."""
    try:
        url = f"https://www.youtube.com/feed/trending?gl={region_code}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        from openbiliclaw.network import outbound_httpx_kwargs

        with httpx.Client(timeout=15, **outbound_httpx_kwargs()) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            html = response.text
        return _extract_innertube_config(html)
    except Exception as exc:
        logger.debug("Failed to read YouTube InnerTube config; using fallback: %s", exc)
        return InnerTubeConfig()


def _extract_innertube_config(html: str) -> InnerTubeConfig:
    """Extract InnerTube config constants from a YouTube HTML response."""
    api_key = _extract_js_string(html, "INNERTUBE_API_KEY") or _INNERTUBE_KEY
    client_version = (
        _extract_js_string(html, "INNERTUBE_CLIENT_VERSION") or _INNERTUBE_CLIENT_VERSION
    )
    client_name_header = _extract_js_number(html, "INNERTUBE_CONTEXT_CLIENT_NAME") or "1"
    return InnerTubeConfig(
        api_key=api_key,
        client_version=client_version,
        client_name_header=client_name_header,
    )


def _extract_js_string(html: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', html)
    return match.group(1) if match else ""


def _extract_js_number(html: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*(\d+)', html)
    return match.group(1) if match else ""


def _extract_innertube_videos(data: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    """Walk InnerTube's nested renderer tree and extract video renderer dicts."""
    results: list[dict[str, Any]] = []
    _walk(data, results, limit)
    return results


def _walk(node: Any, out: list[dict[str, Any]], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(node, dict):
        if "videoId" in node and "title" in node:
            out.append(node)
            return
        for v in node.values():
            _walk(v, out, limit)
    elif isinstance(node, list):
        for item in node:
            if len(out) >= limit:
                return
            _walk(item, out, limit)


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


@dataclass
class YtScraperClient:
    """Async YouTube discovery client backed by scrapetube + InnerTube API."""

    region_code: str = _DEFAULT_REGION
    _executor: Any = field(default=None, init=False, repr=False)

    async def search_videos(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(None, partial(_scrapetube_search, query, limit))
        return _dict_rows(rows)

    async def get_trending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(
            None, partial(_innertube_trending, self.region_code, limit)
        )
        return _dict_rows(rows)

    async def get_channel_videos(self, channel_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(None, partial(_scrapetube_channel, channel_id, limit))
        return _dict_rows(rows)


def _dict_rows(value: object) -> list[dict[str, Any]]:
    """Keep malformed third-party rows behind the source-client boundary."""

    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]
