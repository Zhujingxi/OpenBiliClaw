"""Tests for generic BrowserManager with Playwright CDP + agent-browser backends."""

from __future__ import annotations

from typing import Any

import pytest

from openbiliclaw.sources import browser as browser_module
from openbiliclaw.sources.browser import BrowserManager


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text
        self.goto_calls: list[tuple[str, dict[str, Any]]] = []

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_calls.append((url, kwargs))

    async def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
        return None

    async def evaluate(self, script: str) -> dict[str, Any]:
        return {"text": self._text, "anchors": []}

    async def close(self) -> None:
        return None


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.pages: list[_FakePage] = []

    async def new_page(self) -> _FakePage:
        self.pages.append(self._page)
        return self._page


class _FakeBrowser:
    def __init__(self, page_text: str, *, contexts_empty: bool = False) -> None:
        self._page = _FakePage(page_text)
        self._context = _FakeContext(self._page)
        self.contexts: list[_FakeContext] = [] if contexts_empty else [self._context]
        self.closed = False
        self.new_contexts: list[_FakeContext] = []

    async def new_context(self) -> _FakeContext:
        ctx = _FakeContext(self._page)
        self.new_contexts.append(ctx)
        return ctx

    async def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser
        self.connect_urls: list[str] = []

    async def connect_over_cdp(self, url: str) -> _FakeBrowser:
        self.connect_urls.append(url)
        return self._browser


class _FakePlaywright:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.chromium = _FakeChromium(browser)

    async def stop(self) -> None:
        return None


class _FakePlaywrightManager:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._pw = _FakePlaywright(browser)

    async def __aenter__(self) -> _FakePlaywright:
        return self._pw

    async def __aexit__(self, *exc: object) -> None:
        return None


def _install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch,
    browser: _FakeBrowser,
) -> _FakePlaywrightManager:
    manager = _FakePlaywrightManager(browser)

    def fake_async_playwright() -> _FakePlaywrightManager:
        return manager

    monkeypatch.setattr(browser_module, "_async_playwright", fake_async_playwright)
    return manager


class TestBrowserManagerCDPBackend:
    """CDP backend: connect to pre-launched Chrome, reuse logged-in context."""

    def test_cdp_url_makes_manager_available_without_agent_browser(self) -> None:
        manager = BrowserManager(cdp_url="http://127.0.0.1:9222")
        assert manager.is_available is True

    def test_no_cdp_and_no_agent_browser_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("openbiliclaw.bilibili.browser.shutil.which", lambda _name: None)
        manager = BrowserManager()
        assert manager.is_available is False

    @pytest.mark.asyncio
    async def test_get_page_text_uses_cdp_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_browser = _FakeBrowser("hello from logged-in xhs")
        manager = _install_fake_playwright(monkeypatch, fake_browser)

        bm = BrowserManager(cdp_url="http://127.0.0.1:9222")
        text = await bm.get_page_text("https://www.xiaohongshu.com/explore")

        assert text == "hello from logged-in xhs"
        assert manager._pw.chromium.connect_urls == ["http://127.0.0.1:9222"]
        # Reused existing context (logged-in), did NOT create a new one
        assert fake_browser.new_contexts == []
        assert fake_browser.contexts[0].pages == [fake_browser._page]
        # Playwright's ``browser.close()`` on a connect_over_cdp session is
        # really a detach — it releases the CDP connection without killing
        # the host Chrome. The fake can't distinguish, but we do call it.
        assert fake_browser.closed is True

    @pytest.mark.asyncio
    async def test_get_page_snapshot_returns_text_and_anchors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Emulate what the real JS returns: a dict with text + anchor list
        class _AnchorPage(_FakePage):
            async def evaluate(self, script: str) -> dict[str, Any]:
                return {
                    "text": "visible body",
                    "anchors": [
                        {"text": "  first note  ", "href": "https://example.com/a"},
                        {"text": "", "href": "https://example.com/empty-text"},
                        {"text": "second", "href": ""},
                        {"text": "third", "href": "https://example.com/b"},
                    ],
                }

        class _AnchorBrowser(_FakeBrowser):
            def __init__(self) -> None:
                super().__init__("")
                self._page = _AnchorPage("")
                self._context = _FakeContext(self._page)
                self.contexts = [self._context]

        fake_browser = _AnchorBrowser()
        _install_fake_playwright(monkeypatch, fake_browser)

        bm = BrowserManager(cdp_url="http://127.0.0.1:9222")
        snap = await bm.get_page_snapshot("https://example.com")

        assert snap.text == "visible body"
        # Only rows with BOTH text and href (after trimming) survive
        assert snap.anchors == [
            ("first note", "https://example.com/a"),
            ("third", "https://example.com/b"),
        ]

    @pytest.mark.asyncio
    async def test_get_page_text_creates_context_if_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_browser = _FakeBrowser("fallback page", contexts_empty=True)
        _install_fake_playwright(monkeypatch, fake_browser)

        bm = BrowserManager(cdp_url="http://127.0.0.1:9222")
        text = await bm.get_page_text("https://example.com")

        assert text == "fallback page"
        assert len(fake_browser.new_contexts) == 1

    @pytest.mark.asyncio
    async def test_cdp_backend_does_not_invoke_agent_browser(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_browser = _FakeBrowser("x")
        _install_fake_playwright(monkeypatch, fake_browser)

        def fail_exec(*args: object, **kwargs: object) -> None:
            raise AssertionError("agent-browser must not run when cdp_url is set")

        monkeypatch.setattr(
            "openbiliclaw.bilibili.browser.asyncio.create_subprocess_exec", fail_exec
        )

        bm = BrowserManager(cdp_url="http://127.0.0.1:9222")
        await bm.get_page_text("https://example.com")


class TestBrowserManagerAgentBrowserFallback:
    """Without cdp_url, BrowserManager must keep wrapping agent-browser."""

    @pytest.mark.asyncio
    async def test_falls_back_to_agent_browser_when_cdp_url_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        async def fake_get_page_content(self: Any, url: str) -> str:
            calls.append(url)
            return "agent-browser snapshot text"

        monkeypatch.setattr(
            "openbiliclaw.bilibili.browser.BilibiliBrowser.get_page_content",
            fake_get_page_content,
        )

        bm = BrowserManager()
        text = await bm.get_page_text("https://example.com")

        assert text == "agent-browser snapshot text"
        assert calls == ["https://example.com"]
