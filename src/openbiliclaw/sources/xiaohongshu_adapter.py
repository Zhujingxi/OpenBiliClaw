"""Xiaohongshu (小红书) source adapter — HTTP client for the GPL-isolated sidecar.

Detail enrichment is delegated to ``sidecar/xhs-downloader`` over HTTP. The
main backend **never** imports xhs-downloader code; the sidecar is the only
place that imports the GPL-3.0 library.

Discovery (finding note URLs) happens in the user's own browser via the
extension's task executor — not here. This adapter's only responsibility is
turning a list of already-discovered URLs into ``DiscoveredContent`` items.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from openbiliclaw.discovery.engine import DiscoveredContent

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)

_DETAIL_ENDPOINT = "/xhs/detail"
_CONCURRENCY = 2
_PER_CALL_TIMEOUT_SECS = 30.0
_KEY_TITLE = ("作品标题", "title", "note_title")
_KEY_AUTHOR = ("作者昵称", "author", "nickname", "user_nickname")
_KEY_URL = ("作品链接", "note_url", "url")
_KEY_DESCRIPTION = ("作品描述", "description", "desc")
_KEY_TAGS = ("作品标签", "tags", "tag_list")


class XiaohongshuAdapter:
    """Adapter that enriches xhs note URLs via the sidecar's /xhs/detail endpoint.

    Supported recipe strategies:

    - ``enrich``: ``recipe.config["urls"]: list[str]`` — POSTs each URL to
      the sidecar and maps the response into ``DiscoveredContent``.

    Any other strategy currently returns ``[]``. Backend-driven search is
    intentionally unsupported here: it would trigger xhs risk control.
    Search-like discovery must happen in the extension (see Task 6 / 7).
    """

    def __init__(
        self,
        *,
        sidecar_url: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._sidecar_url = (sidecar_url or "").rstrip("/") or None
        self._client = client
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(_CONCURRENCY)

    @property
    def source_type(self) -> str:
        return "xiaohongshu"

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: SoulProfile,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        if not self._sidecar_url:
            logger.warning(
                "XiaohongshuAdapter: sidecar_url not configured, returning empty"
            )
            return []

        if recipe.strategy != "enrich":
            logger.info(
                "XiaohongshuAdapter: strategy %r not supported in backend; "
                "discovery must happen via the extension",
                recipe.strategy,
            )
            return []

        urls_raw = recipe.config.get("urls", []) if recipe.config else []
        urls = [u for u in urls_raw if isinstance(u, str) and u]
        if not urls:
            return []

        urls = urls[:limit]

        client = self._client or httpx.AsyncClient(timeout=_PER_CALL_TIMEOUT_SECS)
        try:
            tasks = [self._enrich_one(client, url) for url in urls]
            results = await asyncio.gather(*tasks, return_exceptions=False)
        finally:
            if self._owns_client and self._client is None:
                await client.aclose()

        return [item for item in results if item is not None]

    async def _enrich_one(
        self, client: httpx.AsyncClient, url: str
    ) -> DiscoveredContent | None:
        async with self._semaphore:
            try:
                response = await client.post(
                    f"{self._sidecar_url}{_DETAIL_ENDPOINT}",
                    json={"url": url},
                    timeout=_PER_CALL_TIMEOUT_SECS,
                )
            except httpx.HTTPError:
                logger.warning("xhs sidecar request failed for %s", url, exc_info=True)
                return None

            if response.status_code != 200:
                logger.warning(
                    "xhs sidecar HTTP %s for %s", response.status_code, url
                )
                return None

            try:
                payload = response.json()
            except ValueError:
                logger.warning("xhs sidecar returned non-JSON for %s", url)
                return None

        if not isinstance(payload, dict) or not payload.get("ok"):
            logger.info(
                "xhs sidecar reported failure for %s: %s",
                url,
                payload.get("error") if isinstance(payload, dict) else payload,
            )
            return None

        data = payload.get("data")
        if not isinstance(data, dict):
            logger.warning("xhs sidecar returned ok=True without data for %s", url)
            return None

        return _map_note_to_content(data, fallback_url=url)


def _first(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _coerce_tags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(t) for t in raw if t]
    if isinstance(raw, str):
        return [chunk for chunk in (part.strip() for part in raw.split(",")) if chunk]
    return []


def _extract_note_id(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    path = parsed.path.strip("/")
    if not path:
        return ""
    return path.rsplit("/", 1)[-1]


def _map_note_to_content(data: dict[str, Any], *, fallback_url: str) -> DiscoveredContent:
    title = _first(data, _KEY_TITLE) or ""
    author = _first(data, _KEY_AUTHOR) or ""
    note_url = _first(data, _KEY_URL) or fallback_url
    description = _first(data, _KEY_DESCRIPTION) or ""
    tags = _coerce_tags(_first(data, _KEY_TAGS))
    note_id = _extract_note_id(note_url) or _extract_note_id(fallback_url)

    return DiscoveredContent(
        title=str(title),
        up_name=str(author),
        description=str(description),
        tags=tags,
        content_id=note_id,
        content_url=str(note_url),
        source_platform="xiaohongshu",
        author_name=str(author),
        source_strategy="enrich",
    )
