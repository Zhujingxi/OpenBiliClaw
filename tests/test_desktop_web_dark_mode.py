import re
from pathlib import Path

APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")
CLASSIC_CSS = Path("src/openbiliclaw/web/desktop/assets/css/classic.css")
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


def test_accent_bootstrap_defaults_classic_and_migrates_existing_hue_before_css() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    bootstrap = html[: html.index('<link rel="stylesheet" href="/web/assets/css/app.css">')]

    assert 'let accent = "classic";' in bootstrap
    assert 'storage?.getItem("obc.accentStyle")' in bootstrap
    assert 'const hasCustomHue = hue !== null && hue !== "";' in bootstrap
    assert 'hasCustomHue ? "modern" : "classic"' in bootstrap
    assert 'storage?.setItem("obc.accentStyle", accent)' in bootstrap
    assert 'document.documentElement.dataset.accent = "classic"' in bootstrap
    assert html.index("obc.accentStyle") < html.index(
        '<link rel="stylesheet" href="/web/assets/css/classic.css">'
    )

    assert 'const ACCENT_OPTIONS = ["modern", "classic"];' in js
    assert 'const _hasCustomHue = storageGet(THEME_HUE_STORAGE_KEY) !== "";' in js
    assert "ACCENT_OPTIONS.includes(_storedAccent)" in js
    assert "storageSet(ACCENT_STORAGE_KEY, state.accentStyle);" in js


def test_classic_theme_notice_is_transient_and_separate_from_action_toasts() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    notice_start = html.index('id="themeNotice"')
    notice_end = html.index("</aside>", notice_start)
    toast_start = html.index('id="toastContainer"')

    assert notice_end < toast_start
    assert 'id="themeNoticeDismiss"' in html
    assert 'id="themeNoticeSettings"' in html
    assert 'const THEME_NOTICE_DISMISSED_KEY = "obc.noticeDismissed";' in js
    assert "storageGet(THEME_NOTICE_DISMISSED_KEY)" in js
    assert 'storageSet(THEME_NOTICE_DISMISSED_KEY, "1")' in js
    assert "const THEME_NOTICE_DURATION_MS = 8000;" in js
    assert 'notice.addEventListener("focusin", clearTimer)' in js
    assert "showNoticeToast" not in js
    assert 'localStorage.getItem("obc.noticeDismissed")' not in js


def test_theme_choices_and_native_selects_keep_keyboard_accessibility() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")
    classic_css = CLASSIC_CSS.read_text(encoding="utf-8")

    assert 'id="themeModeSetting" role="radiogroup"' in html
    assert 'id="themeAccentSetting" role="radiogroup"' in html
    assert 'id="themeHueSetting" role="radiogroup"' in html
    assert html.count('role="radio"') >= 17
    assert "bindRovingChoiceGroup" in js
    for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"):
        assert key in js
    assert "enhanceSelects" not in js
    assert ".custom-select" not in css
    assert ".settings-field select:focus-visible" in css
    assert "border-color: var(--contrast-strong);" in classic_css
    assert "box-shadow: var(--focus-ring);" in classic_css


def test_coarse_pointer_save_controls_keep_minimum_touch_targets() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert "@media (max-width: 820px), (pointer: coarse)" in css
    assert ".feedback-icon-btn.watch-later-btn" in css
    assert ".feedback-icon-btn.favorite-btn" in css
    assert "flex-basis: 44px; width: 44px; min-width: 44px;" in css


def test_classic_primary_text_meets_wcag_aa_contrast() -> None:
    css = CLASSIC_CSS.read_text(encoding="utf-8")
    light_block = css.split(':root[data-accent="classic"] {', 1)[1].split("}", 1)[0]
    dark_block = css.split(':root[data-accent="classic"][data-theme="dark"] {', 1)[1].split("}", 1)[
        0
    ]

    def hex_token(block: str, name: str) -> str:
        match = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}});", block)
        assert match is not None
        return match.group(1)

    def luminance(color: str) -> float:
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    for block in (light_block, dark_block):
        accent_luminance = luminance(hex_token(block, "accent"))
        for foreground in ("accent-on", "badge-on"):
            foreground_luminance = luminance(hex_token(block, foreground))
            ratio = (max(accent_luminance, foreground_luminance) + 0.05) / (
                min(accent_luminance, foreground_luminance) + 0.05
            )
            assert ratio >= 4.5
