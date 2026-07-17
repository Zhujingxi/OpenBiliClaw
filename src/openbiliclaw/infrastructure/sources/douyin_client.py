"""Direct-cookie Douyin discovery client retained behind the vNext connector."""

from __future__ import annotations

import logging
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from openbiliclaw.infrastructure.sources.douyin_signing import XBogusSigner

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class DouyinDirectError(RuntimeError):
    """Base error for direct-cookie Douyin discovery."""


class DouyinDirectAuthError(DouyinDirectError):
    """Raised when direct-cookie mode has no usable cookie."""


class DouyinDirectSignatureError(DouyinDirectError):
    """Raised when URL signing fails."""


class UrlSigner(Protocol):
    user_agent: str

    def sign(self, url: str) -> str: ...


def parse_cookie_header(cookie: str) -> dict[str, str]:
    """Parse a browser Cookie header into name/value pairs."""
    pairs: dict[str, str] = {}
    for part in cookie.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not value:
            continue
        pairs[name] = value
    return pairs


class DouyinDirectClient:
    """Small direct-cookie Douyin Web API client.

    The client intentionally covers only discovery surfaces. It is not a
    downloader and does not persist cookies.
    """

    BASE_URL = "https://www.douyin.com"

    def __init__(
        self,
        *,
        cookie: str,
        http_client: httpx.AsyncClient | None = None,
        signer: UrlSigner | None = None,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        self.cookie = cookie.strip()
        self.cookies = parse_cookie_header(self.cookie)
        if not self.cookie or not self.cookies:
            raise DouyinDirectAuthError("Douyin direct discovery requires a cookie.")

        self._owns_http_client = http_client is None
        # douyin.com is a CN domain: never inherit env/system proxies — proxy
        # exit IPs trip Douyin risk control the same way they broke the B站
        # login probe (see bilibili/api.py). Injected clients are the
        # caller's responsibility.
        self._http = http_client or httpx.AsyncClient(timeout=30.0, trust_env=False)
        self._signer = signer or XBogusSigner(user_agent)
        self._user_agent = self._signer.user_agent

    async def __aenter__(self) -> DouyinDirectClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, Any]]:
        """Fetch Douyin video search results for *keyword*."""
        if not keyword.strip() or limit <= 0:
            return []

        collected: list[dict[str, Any]] = []
        offset = 0
        while len(collected) < limit:
            count = min(20, limit - len(collected))
            data = await self._request_json(
                "/aweme/v1/web/general/search/single/",
                {
                    "search_channel": "aweme_video_web",
                    "keyword": keyword,
                    "search_source": "normal_search",
                    "query_correct_type": 1,
                    "is_filter_search": 0,
                    "offset": offset,
                    "count": count,
                },
            )
            page_items = _extract_search_items(data)
            collected.extend(page_items)
            if len(collected) >= limit or not _has_more(data):
                break
            next_offset = _cursor_value(data)
            if next_offset == offset:
                break
            offset = next_offset
        return _dedupe_awemes(collected)[:limit]

    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """Fetch aweme entries attached to Douyin hot-search board rows."""
        if limit <= 0:
            return []
        data = await self._request_json(
            "/aweme/v1/web/hot/search/list/",
            {
                "detail_list": 1,
                "source": 6,
            },
        )
        return _dedupe_awemes(_extract_hot_awemes(data))[:limit]

    async def get_hot_terms(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """Fetch Douyin hot-search rows with ``sentence_id`` for /hot routing."""
        if limit <= 0:
            return []
        data = await self._request_json(
            "/aweme/v1/web/hot/search/list/",
            {
                "detail_list": 1,
                "source": 6,
            },
        )
        return _extract_hot_terms(data)[:limit]

    async def get_creator_posts(self, sec_uid: str, *, limit: int = 30) -> list[dict[str, Any]]:
        """Fetch recent posts from a creator's ``sec_uid``."""
        sec_uid = sec_uid.strip()
        if not sec_uid or limit <= 0:
            return []

        collected: list[dict[str, Any]] = []
        max_cursor = 0
        while len(collected) < limit:
            count = min(20, limit - len(collected))
            data = await self._request_json(
                "/aweme/v1/web/aweme/post/",
                {
                    "sec_user_id": sec_uid,
                    "max_cursor": max_cursor,
                    "count": count,
                    "locate_query": "false",
                    "show_live_replay_strategy": 1,
                },
            )
            page_items = data.get("aweme_list")
            if not isinstance(page_items, list):
                break
            collected.extend(item for item in page_items if isinstance(item, dict))
            if len(collected) >= limit or not _has_more(data):
                break
            next_cursor = _cursor_value(data)
            if next_cursor == max_cursor:
                break
            max_cursor = next_cursor
        return _dedupe_awemes(collected)[:limit]

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """Fetch the Douyin home recommendation feed via direct-cookie mode.

        The browser-plugin path is preferred for this source because the
        endpoint is sensitive to page session state. This fallback keeps
        the client protocol complete for diagnostics.
        """
        if limit <= 0:
            return []
        data = await self._request_json(
            "/aweme/v1/web/tab/feed/",
            {
                "count": min(20, max(1, limit)),
                "tag_id": "",
                "share_aweme_id": "",
                "live_insert_type": "",
                "refresh_index": 1,
                "video_type_select": 1,
                "aweme_pc_rec_raw_data": '{"is_client":"false"}',
                "globalwid": "",
                "pull_type": "",
                "min_window": "",
                "free_right": "",
                "ug_source": "",
                "creative_id": "",
            },
        )
        raw_items = data.get("aweme_list")
        if not isinstance(raw_items, list):
            raw_items = data.get("data")
        if not isinstance(raw_items, list):
            return []
        items = [item for item in raw_items if isinstance(item, dict)]
        return _dedupe_awemes(items)[:limit]

    async def _request_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {**self._default_query(), **params}
        unsigned = f"{self.BASE_URL}{path}?{urlencode(query)}"
        try:
            url = self._signer.sign(unsigned)
        except Exception as exc:  # pragma: no cover - defensive seam for live signer drift
            raise DouyinDirectSignatureError("Failed to sign Douyin request URL.") from exc

        try:
            response = await self._http.get(
                url,
                headers={
                    "Accept": "application/json",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Cookie": self.cookie,
                    "Referer": "https://www.douyin.com/",
                    "User-Agent": self._user_agent,
                },
            )
        except httpx.HTTPError as exc:
            logger.info("douyin direct request failed for %s: %s", path, exc)
            return {}
        if response.status_code != 200:
            logger.info("douyin direct request returned HTTP %s for %s", response.status_code, path)
            return {}
        try:
            data = response.json()
        except ValueError:
            logger.info("douyin direct request returned non-JSON body for %s", path)
            return {}
        return data if isinstance(data, dict) else {}

    def _default_query(self) -> dict[str, Any]:
        return {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "pc_client_type": "1",
            "version_code": "290100",
            "version_name": "29.1.0",
            "cookie_enabled": "true",
            "screen_width": "1920",
            "screen_height": "1080",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "131.0.0.0",
            "browser_online": "true",
            "engine_name": "Blink",
            "engine_version": "131.0.0.0",
            "os_name": "Windows",
            "os_version": "10",
            "platform": "PC",
            "msToken": self.cookies.get("msToken", ""),
        }


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value or 0)))
    except (TypeError, ValueError):
        return 0


def _has_more(data: dict[str, Any]) -> bool:
    value = data.get("has_more", False)
    if isinstance(value, bool):
        return value
    return _to_int(value) > 0


def _cursor_value(data: dict[str, Any]) -> int:
    return _to_int(data.get("cursor", data.get("max_cursor", 0)))


def _extract_search_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = data.get("data")
    if not isinstance(raw_items, list):
        raw_items = data.get("aweme_list")
    if not isinstance(raw_items, list):
        return []

    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        aweme = raw.get("aweme_info") or raw.get("item") or raw
        if isinstance(aweme, dict):
            items.append(aweme)
    return items


def _extract_hot_awemes(data: dict[str, Any]) -> list[dict[str, Any]]:
    container = data.get("data")
    if isinstance(container, dict):
        raw_items = container.get("word_list") or container.get("trending_list")
    else:
        raw_items = data.get("word_list")
    if not isinstance(raw_items, list):
        return []

    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        aweme = raw.get("aweme_info") or raw.get("aweme")
        if isinstance(aweme, dict):
            items.append(aweme)
    return items


def _extract_hot_terms(data: dict[str, Any]) -> list[dict[str, Any]]:
    container = data.get("data")
    if isinstance(container, dict):
        raw_items = container.get("word_list") or container.get("trending_list")
    else:
        raw_items = data.get("word_list")
    if not isinstance(raw_items, list):
        return []

    terms: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        sentence_id = _first_text(raw.get("sentence_id"), raw.get("sentenceId"), raw.get("id"))
        if not sentence_id or sentence_id in seen:
            continue
        seen.add(sentence_id)

        term: dict[str, Any] = {
            "word": _first_text(raw.get("word"), raw.get("sentence"), raw.get("event_word")),
            "sentence_id": sentence_id,
        }
        for key in ("hot_value", "position", "rank", "event_time"):
            if key in raw:
                term[key] = raw[key]
        seed_aweme_id = _first_text(raw.get("group_id"), raw.get("aweme_id"))
        if seed_aweme_id:
            term["group_id"] = seed_aweme_id
            term["seed_aweme_id"] = seed_aweme_id
        aweme = raw.get("aweme_info") or raw.get("aweme")
        if isinstance(aweme, dict):
            term["aweme"] = aweme
        terms.append(term)
    return terms


def _dedupe_awemes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        aweme_id = str(item.get("aweme_id", "") or "").strip()
        if not aweme_id or aweme_id in seen:
            continue
        seen.add(aweme_id)
        deduped.append(item)
    return deduped
