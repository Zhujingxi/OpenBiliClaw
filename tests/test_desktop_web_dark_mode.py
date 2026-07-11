from pathlib import Path

APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")
APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")


def test_dark_mode_css_declares_explicit_and_auto_token_blocks() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert ':root[data-theme="dark"] {' in css
    assert "@media (prefers-color-scheme: dark)" in css
    assert ':root:not([data-theme="light"])' in css
    assert "keep both blocks in sync -- no build step to dedupe" in css
    assert "color-scheme: dark;" in css


def test_dark_mode_css_tokenizes_theme_color_stragglers() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert "--probe-challenge:" in css
    assert "--probe-avoidance:" in css
    assert "--star-active:" in css
    assert "--overlay-faint:" in css
    assert "color: #6d28d9;" not in css
    assert "color: #1d4ed8;" not in css
    assert '.favorite-btn[aria-pressed="true"] { color: var(--star-active); }' in css


def test_index_bootstraps_theme_before_stylesheet_without_flash() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert '<meta name="color-scheme" content="light dark">' in html
    assert "obc.theme" in html
    assert "document.documentElement.dataset.theme" in html
    assert html.index("obc.theme") < html.index(
        '<link rel="stylesheet" href="/web/assets/css/app.css">'
    )
    assert (
        "<script src="
        not in html[: html.index('<link rel="stylesheet" href="/web/assets/css/app.css">')]
    )


def test_app_js_contains_three_state_theme_cycle_and_storage_key() -> None:
    js = APP_JS.read_text(encoding="utf-8")

    assert 'const THEME_STORAGE_KEY = "obc.theme";' in js
    assert 'const THEME_OPTIONS = ["auto", "light", "dark"];' in js
    assert "storageGet(THEME_STORAGE_KEY)" in js
    assert "storageSet(THEME_STORAGE_KEY, state.themeMode);" in js
    assert "cycleThemeMode" in js
    assert "跟随系统" in js
    assert "浅色" in js
    assert "深色" in js


def test_theme_controls_exist_in_topbar_and_frontend_settings() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")

    assert 'id="themeToggleBtn"' in html
    assert 'id="themeModeSetting"' in html
    assert 'data-theme-choice="auto"' in html
    assert 'data-theme-choice="light"' in html
    assert 'data-theme-choice="dark"' in html
    assert 'safeBind("#themeToggleBtn", "click", cycleThemeMode);' in js
    assert "renderThemeControls();" in js
