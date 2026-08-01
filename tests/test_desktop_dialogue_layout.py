from pathlib import Path

APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")


def test_desktop_dialogue_has_a_structured_pending_inbox() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    assert 'class="dialogue-pending-toggle-title"' in html
    assert 'class="dialogue-pending-toggle-count" id="desktopPendingCount"' in html
    assert 'class="dialogue-pending-toggle-chevron"' in html
    assert "这些判断需要你点头" not in html
    assert ".dialogue-pending {" in css
    assert "#desktopPendingConfirmations {" in css
    assert "grid-template-columns: 1fr;" in css
    assert "padding: 0 8px 8px;" in css
    assert "border-radius: 13px;" in css
    assert "#desktopPendingToggle:focus-visible" in css


def test_desktop_dialogue_cards_have_hierarchy_and_responsive_actions() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert ".dialogue-card {" in css
    assert ".dialogue-card-title {" in css
    assert ".dialogue-card-actions {" in css
    assert ".dialogue-card-action.is-confirm {" in css
    assert ".dialogue-card-action.is-reject:hover {" in css
    assert ".dialogue-card-action.is-defer {" in css
    assert '.dialogue-card[data-card-state="confirmed"]' in css
    assert ".dialogue-card-actions { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in css


def test_desktop_dialogue_composer_has_an_accessible_name() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="chatInput" aria-label="和阿B聊聊你的口味"' in html
