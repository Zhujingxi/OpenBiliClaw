import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")


def _function_body(js: str, name: str) -> str:
    match = re.search(rf"function {name}\([^)]*\) \{{(?P<body>.*?)\n    \}}", js, flags=re.S)
    assert match is not None, f"{name} function not found"
    return match.group("body")


def test_index_declares_pending_chat_count_toggle() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="showPendingChatCountSetting" type="checkbox" checked' in html
    assert 'id="showPendingChatCountSettingText">开启' in html
    assert "显示待聊未读数" in html
    # The toggle lives in the frontend settings panel, next to the autoload
    # preference, before the panel's footnote.
    panel_start = html.index('id="settingsPanelFrontend"')
    assert panel_start < html.index('id="showPendingChatCountSetting"') < html.index(
        "settings-note-inline", panel_start
    )


def test_pending_chat_count_setting_uses_frontend_storage_pattern() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    restore_frontend = _function_body(js, "restoreFrontendSettings")
    persist_frontend = _function_body(js, "persistFrontendSettings")

    assert 'const SHOW_PENDING_CHAT_COUNT_KEY = "openbiliclaw.webui.showPendingChatCount";' in js
    assert 'state.showPendingChatCount = storageGet(SHOW_PENDING_CHAT_COUNT_KEY) !== "0";' in js
    assert "renderShowPendingChatCountToggle();" in restore_frontend
    assert (
        'storageSet(SHOW_PENDING_CHAT_COUNT_KEY, state.showPendingChatCount ? "1" : "0");'
        in persist_frontend
    )


def test_pending_chat_count_badge_is_hidden_when_setting_off() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    render = _function_body(js, "renderDesktopPendingConfirmations")

    assert 'if (state.showPendingChatCount) {' in render
    assert 'updateSavedBadge("chatPendingCountBadge", count);' in render
    assert 'badge.setAttribute("hidden", "");' in render


def test_pending_chat_count_toggle_updates_badge_immediately() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    setter = _function_body(js, "setShowPendingChatCount")

    assert "renderShowPendingChatCountToggle();" in setter
    assert "renderDesktopPendingConfirmations();" in setter
