"""Generic browser automation layer for multi-source content fetching.

Two interchangeable backends:

``cdp_url`` set (recommended)
    Connect to a pre-launched Chrome via Playwright ``connect_over_cdp``.
    The user opens Chrome once with ``--remote-debugging-port=9222``,
    logs into the target platforms, and leaves it running. Every adapter
    call then reuses that logged-in session — which is the only way
    sources like Xiaohongshu actually work without getting rate-limited.

``cdp_url`` empty (fallback)
    Wrap the existing agent-browser CLI. No login state — fine for
    simple anonymous pages, blocked on most real sources.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

logger = logging.getLogger(__name__)

# JS evaluated in-page. Returns both the visible body text AND every
# clickable anchor as {text, href}. The LLM extractor works on the
# inner text only, but callers use the anchor list to backfill the
# ``content_url`` field — innerText alone drops all hrefs, which means
# extracted items otherwise have no way to link back to source.
_PAGE_SNAPSHOT_SCRIPT = """\
() => {
  const text = (document.body && document.body.innerText) || '';
  const seen = new Set();
  const anchors = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href || '';
    if (!href || href.startsWith('javascript:') || seen.has(href)) continue;
    const t = ((a.innerText || a.textContent || '') + '').trim();
    if (!t) continue;
    seen.add(href);
    anchors.push({text: t.slice(0, 200), href: href});
  }
  return {text: text, anchors: anchors};
}
"""


@dataclass
class PageSnapshot:
    """Page content + anchor metadata captured in a single round trip.

    ``text`` mirrors ``document.body.innerText`` (what LLM extractors chew
    on). ``anchors`` preserves the ``(visible_text, href)`` pairs that
    innerText throws away — callers use these to rebuild URLs for items
    the extractor surfaces.
    """

    text: str
    anchors: list[tuple[str, str]] = field(default_factory=list)


def _async_playwright() -> Any:
    """Lazily import ``playwright.async_api.async_playwright``.

    Kept as a module-level function so tests can monkey-patch it
    without touching the optional playwright dependency.
    """
    try:
        async_playwright = import_module("playwright.async_api").async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright not installed. Install with: "
            "pip install 'openbiliclaw[browser]' "
            "and then: playwright install chromium"
        ) from exc
    return async_playwright()


class BrowserManager:
    """Manages browser sessions for non-Bilibili content sources.

    Args:
        executable: agent-browser executable path (fallback backend only).
        headed: whether to launch agent-browser headed (fallback backend only).
        cdp_url: CDP WebSocket/HTTP endpoint of a pre-launched Chrome.
            Example: ``http://127.0.0.1:9222``. When set, this backend
            takes precedence over agent-browser.
    """

    def __init__(
        self,
        executable: str = "",
        headed: bool = False,
        cdp_url: str = "",
    ) -> None:
        self._cdp_url = cdp_url.strip()

        if not self._cdp_url:
            from openbiliclaw.bilibili.browser import BilibiliBrowser

            self._browser: Any = BilibiliBrowser(
                executable=executable,
                headed=headed,
                cookie="",
            )
        else:
            self._browser = None

    @property
    def is_available(self) -> bool:
        """Whether the chosen backend can be invoked.

        For the CDP backend, availability is determined lazily at call time
        (connection may still fail if the Chrome instance is not running);
        for the agent-browser backend we delegate to its own check.
        """
        if self._cdp_url:
            return True
        return bool(self._browser and self._browser.is_available)

    @property
    def backend(self) -> str:
        """Backend identifier: ``"cdp"`` or ``"agent-browser"``."""
        return "cdp" if self._cdp_url else "agent-browser"

    async def get_page_snapshot(self, url: str) -> PageSnapshot:
        """Navigate to ``url`` and return text + anchors.

        The CDP backend captures both in one JS evaluate; the agent-browser
        fallback only exposes text, so ``anchors`` is returned empty.
        """
        if self._cdp_url:
            return await self._get_page_snapshot_cdp(url)
        assert self._browser is not None
        text: str = await self._browser.get_page_content(url)
        return PageSnapshot(text=text, anchors=[])

    async def get_page_text(self, url: str) -> str:
        """Navigate to ``url`` and return visible page text only.

        Thin wrapper over :meth:`get_page_snapshot` for callers that
        don't need anchor data.
        """
        snapshot = await self.get_page_snapshot(url)
        return snapshot.text

    async def close(self) -> None:
        """Close the fallback backend; CDP backend detaches per-call."""
        if self._cdp_url:
            return
        if self._browser is not None:
            await self._browser.close()

    async def _get_page_snapshot_cdp(self, url: str) -> PageSnapshot:
        """Connect to the running Chrome via CDP, navigate, return snapshot."""
        async with _async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(self._cdp_url)
            try:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        # Many SPA feeds never go idle — DOMContentLoaded is enough
                        # to give the JS extractor something to chew on.
                        logger.debug("networkidle timeout for %s; proceeding", url)
                    raw = await page.evaluate(_PAGE_SNAPSHOT_SCRIPT)
                finally:
                    try:
                        await page.close()
                    except Exception:
                        logger.debug("failed to close CDP page", exc_info=True)
            finally:
                # ``close()`` on a CDP-connected browser only detaches — it
                # does NOT terminate the host Chrome.
                try:
                    await browser.close()
                except Exception:
                    logger.debug("failed to detach CDP browser", exc_info=True)

        if not isinstance(raw, dict):
            raise RuntimeError(f"CDP backend returned non-dict snapshot: {type(raw)!r}")
        text = raw.get("text", "")
        if not isinstance(text, str):
            raise RuntimeError(f"CDP snapshot .text is not a string: {type(text)!r}")
        anchors_raw = raw.get("anchors", []) or []
        anchors: list[tuple[str, str]] = []
        for entry in anchors_raw:
            if not isinstance(entry, dict):
                continue
            t = str(entry.get("text") or "").strip()
            h = str(entry.get("href") or "").strip()
            if t and h:
                anchors.append((t, h))
        return PageSnapshot(text=text, anchors=anchors)
