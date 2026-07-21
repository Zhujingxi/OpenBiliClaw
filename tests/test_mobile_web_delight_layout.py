"""Static regression tests for the mobile web delight tray layout."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOMMEND_JS = ROOT / "src/openbiliclaw/web/js/views/recommend.ts"
APP_CSS = ROOT / "src/openbiliclaw/web/css/app.css"


def _css_block(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{[\s\S]*?\}}", css)
    return match.group(0) if match else ""


def test_mobile_delight_tray_uses_featured_reason_wrap() -> None:
    """The surprise recommendation tray should look distinct from normal cards."""

    js = RECOMMEND_JS.read_text()
    css = APP_CSS.read_text()

    assert 'class="delight-feature-copy"' in js
    assert 'class="delight-reason-label"' in js
    assert 'id="delight-later"' in js
    assert "\\u7A0D\\u540E\\u770B" in js or "稍后看" in js
    assert 'class="delight-result-state"' in js

    tray_block = _css_block(css, ".delight-tray")
    tag_block = _css_block(css, ".delight-tag")
    wrap_block = _css_block(css, ".delight-reason-wrap")
    reason_block = _css_block(css, ".delight-reason")
    thumb_block = _css_block(css, ".delight-thumb")
    later_block = _css_block(css, ".delight-later-btn")

    assert "linear-gradient" in tray_block
    assert "linear-gradient" in tag_block
    assert "flow-root" in wrap_block
    assert "max-height" not in reason_block
    assert "overflow: hidden" not in reason_block
    assert "float: left" in thumb_block
    assert "position: absolute" in later_block


def test_mobile_delight_inline_chat_uses_shared_session_helper() -> None:
    """Inline delight chat must use the same mobile chat session contract as chat.js."""

    js = RECOMMEND_JS.read_text()

    assert "getMobileChatSession" in js
    assert 'session: "mobile"' not in js
    assert 'fetchChatTurns({ session: "mobile"' not in js


def test_mobile_failed_chat_turn_renders_durable_error() -> None:
    chat_js = (ROOT / "src/openbiliclaw/web/js/views/chat.ts").read_text()

    assert 'turn.status === "error" || turn.status === "failed"' in chat_js
    assert 'errBubble.textContent = turn.error || "\\u56DE\\u590D\\u5931\\u8D25"' in chat_js


def test_mobile_inline_probe_failure_keeps_notification_and_renders_error() -> None:
    chat_js = (ROOT / "src/openbiliclaw/web/js/views/chat.ts").read_text()
    start = chat_js.index("function expandInlineChatOnCard")
    body = chat_js[start:]

    failed_index = body.index('t.status === "failed"')
    completed_index = body.index('t.status === "completed"')
    assert failed_index < completed_index
    failed_branch = body[failed_index:completed_index]
    assert "t.error" in failed_branch
    assert "forgetHandledProbe" in failed_branch
    assert "removeProbeFromNotifications" not in failed_branch


def test_mobile_delight_status_and_actions_render_independently() -> None:
    """Liked delights retain actions while terminal negative/view states can hide them."""

    js = RECOMMEND_JS.read_text()
    css = APP_CSS.read_text()

    assert 'class="delight-result-state"' in js
    assert "if (uiState.show_status)" in js
    assert "if (uiState.show_actions)" in js
    assert "btn.dataset.delightAction = b.action" in js
    assert 'btn.setAttribute("aria-pressed", uiState.like_pressed ? "true" : "false")' in js
    assert "btn.disabled = uiState.like_disabled" in js
    assert "isChatState &&" not in js

    root_block = _css_block(css, ":root")
    liked_block = _css_block(
        css,
        '.delight-actions [data-delight-action="like"][aria-pressed="true"]',
    )
    assert "--accent: var(--brand)" in root_block
    assert "--accent-strong: var(--brand-strong)" in root_block
    assert "border-color: var(--accent)" in liked_block
    assert "background: color-mix" in liked_block
    assert "color: var(--accent-strong)" in liked_block
