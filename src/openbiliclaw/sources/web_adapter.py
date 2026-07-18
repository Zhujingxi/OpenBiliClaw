"""Generic web source adapter — fetches and extracts content from any web page.

Uses a browser backend (Playwright CDP or agent-browser) to load pages
and an LLM to extract structured content. Works for any platform that
doesn't have a dedicated API adapter.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from openbiliclaw.sources.browser import BrowserManager
from openbiliclaw.sources.llm_extractor import extract_content_from_page

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)


class WebSourceAdapter:
    """Generic web content adapter using browser + LLM extraction.

    Recipe config keys:
        url_template: URL pattern, may contain ``{query}`` placeholder.
        query: Search query (substituted into url_template).
        url: Direct URL to fetch (used when url_template is not set).
    """

    def __init__(
        self,
        *,
        llm_service: Any,
        browser_executable: str = "",
        browser_headed: bool = False,
        browser_cdp_url: str = "",
    ) -> None:
        self._llm_service = llm_service
        self._browser_executable = browser_executable
        self._browser_headed = browser_headed
        self._browser_cdp_url = browser_cdp_url

    @property
    def source_type(self) -> str:
        return "web"

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: SoulProfile,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """Fetch content from a web page defined by the recipe."""
        url = self._build_url(recipe)
        if not url:
            logger.warning("WebSourceAdapter: no URL for recipe %s", recipe.id)
            return []

        browser = BrowserManager(
            executable=self._browser_executable,
            headed=self._browser_headed,
            cdp_url=self._browser_cdp_url,
        )

        if not browser.is_available:
            logger.warning(
                "WebSourceAdapter: agent-browser not available, skipping recipe %s",
                recipe.id,
            )
            return []

        try:
            snapshot = await browser.get_page_snapshot(url)
        except Exception:
            logger.exception("WebSourceAdapter: failed to fetch %s", url)
            return []
        finally:
            with suppress(Exception):
                await browser.close()

        items = await extract_content_from_page(
            snapshot.text,
            source_platform=recipe.source_type,
            llm_service=self._llm_service,
            base_url=url,
        )

        # Apply recipe source_type and URL/ID backfill from captured anchors.
        for item in items:
            if not item.source_platform:
                item.source_platform = recipe.source_type
            if not item.content_url:
                matched = _match_anchor_by_title(snapshot.anchors, item.title)
                if matched:
                    item.content_url = matched
            if item.content_url and (not item.content_id or item.content_id == item.title[:32]):
                derived = _extract_content_id(item.content_url)
                if derived:
                    item.content_id = derived

        return items[:limit]

    @staticmethod
    def _build_url(recipe: SourceRecipe) -> str:
        """Build the target URL from recipe config."""
        config = recipe.config or {}
        url_template = str(config.get("url_template", "") or "")
        query = str(config.get("query", "") or "")
        url = str(config.get("url", "") or "")

        if url_template and query:
            return url_template.replace("{query}", query)
        if url:
            return url
        if url_template:
            return url_template
        return ""


def _match_anchor_by_title(
    anchors: list[tuple[str, str]],
    title: str,
) -> str:
    """Return the href of the anchor whose text best matches ``title``.

    Matching is deliberately simple: case-insensitive substring either way
    (anchor text contains the title, or the title contains the anchor
    text). For cards on xiaohongshu / v2ex / zhihu this is sufficient —
    the anchor's visible text IS the card title.
    """
    if not title or not anchors:
        return ""
    needle = title.strip().lower()
    if not needle:
        return ""
    # Prefer exact-substring hits first, then partial overlap, so a card
    # whose full title is a prefix of a longer anchor still wins.
    best_exact = ""
    best_partial = ""
    for text, href in anchors:
        candidate = text.strip().lower()
        if not candidate:
            continue
        if (candidate == needle or needle in candidate) and not best_exact:
            best_exact = href
        elif candidate in needle and not best_partial:
            best_partial = href
    return best_exact or best_partial


def _extract_content_id(url: str) -> str:
    """Pull the last non-empty path segment out of ``url``.

    Works for xiaohongshu (``/explore/{note_id}``, ``/discovery/item/{id}``),
    v2ex (``/t/{topic_id}``), zhihu (``/question/{id}``), etc. Returns ""
    when no usable segment is found — callers should keep the original ID.
    """
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        path = urlparse(url).path.strip("/")
    except Exception:
        return ""
    if not path:
        return ""
    last = path.rsplit("/", 1)[-1]
    return last
