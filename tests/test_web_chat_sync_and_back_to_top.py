"""Regression coverage for the shared Web chat session and top button."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "src/openbiliclaw/web"


def test_mobile_and_desktop_web_expose_the_same_top_button() -> None:
    mobile_html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    desktop_html = (WEB_ROOT / "desktop/index.html").read_text(encoding="utf-8")

    for html in (mobile_html, desktop_html):
        assert 'id="backToTop"' in html
        assert 'aria-label="回到顶部"' in html
        assert "<span>顶部</span>" in html


def test_mobile_top_button_tracks_page_and_chat_scroll_containers() -> None:
    app_js = (WEB_ROOT / "js/app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "css/app.css").read_text(encoding="utf-8")

    assert "function initBackToTop()" in app_js
    assert 'document.getElementById("chat-messages")' in app_js
    assert "target.scrollTo({ top: 0, behavior })" in app_js
    assert 'document.body.classList.toggle("chat-view-active", id === "chat")' in app_js
    assert ".back-to-top[hidden]" in css
    assert "body.chat-view-active .back-to-top" in css
    assert "bottom: calc(160px + var(--safe-bottom));" in css


def test_desktop_web_uses_popup_as_the_shared_chat_session() -> None:
    app_js = (WEB_ROOT / "desktop/assets/js/app.js").read_text(encoding="utf-8")
    api_js = (WEB_ROOT / "js/api.js").read_text(encoding="utf-8")

    assert 'const SHARED_CHAT_SESSION = "popup"' in app_js
    assert "session: SHARED_CHAT_SESSION" in app_js
    assert "encodeURIComponent(SHARED_CHAT_SESSION)" in app_js
    assert 'session = "popup"' in api_js
    assert 'session = "webui"' not in app_js


def test_desktop_top_button_covers_page_and_chat_logs() -> None:
    app_js = (WEB_ROOT / "desktop/assets/js/app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "desktop/assets/css/app.css").read_text(encoding="utf-8")

    assert '$("#chatLog")' in app_js
    assert '$("#messageChatLog")' in app_js
    assert "function initBackToTop()" in app_js
    assert ".back-to-top[hidden]" in css
    assert "body.chat-page-open .back-to-top" in css
    assert "+ 76px" in css


def test_shared_chat_surfaces_poll_history_while_chat_is_open() -> None:
    mobile_chat_js = (WEB_ROOT / "js/views/chat.js").read_text(encoding="utf-8")
    popup_js = (ROOT / "extension/popup/popup.js").read_text(encoding="utf-8")
    desktop_js = (WEB_ROOT / "desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "CHAT_HISTORY_REFRESH_INTERVAL_MS = 2500" in mobile_chat_js
    assert 'state.activeTab !== "chat"' in mobile_chat_js
    assert "void loadHistory()" in mobile_chat_js

    assert "CHAT_HISTORY_REFRESH_INTERVAL_MS = 2500" in popup_js
    assert 'state.activeTab !== "chat"' in popup_js
    assert "void hydrateChatHistory()" in popup_js
    assert "void refreshPendingConfirmations()" in popup_js

    assert "CHAT_HISTORY_REFRESH_INTERVAL_MS = 2500" in desktop_js
    assert "function refreshSharedChatSurface()" in desktop_js
    assert "await refreshDialogueConfirmationSurface()" in desktop_js
