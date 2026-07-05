import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")


def test_main_recommendation_card_cover_is_a_real_link_when_url_exists() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    assert '<a class="cover${recommendationCoverClass(item)}"' in app_js
    assert 'href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"' in app_js
    assert '<button class="cover${recommendationCoverClass(item)}"' in app_js
    assert "后端没有返回可打开链接；点击信号会在后台记录。" in app_js


def test_recommendation_click_tracking_uses_click_and_auxclick_without_window_open() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    open_match = re.search(
        r"function openRecommendation\(item, card\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert open_match is not None, "openRecommendation function not found"
    open_body = open_match.group("body")

    assert "window.open" not in open_body
    assert "preventDefault" not in open_body
    assert "trackRecommendationClick(item);" in open_body
    assert 'addEventListener("click", () => openRecommendation(item, card))' in app_js
    assert 'addEventListener("auxclick", (event)' in app_js
    assert "event.button === 1" in app_js
    assert 'window.open(url, "_blank", "noopener,noreferrer")' not in app_js


def test_saved_message_and_delight_content_opens_use_anchor_semantics() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    assert '<a class="cover"' in app_js
    saved_anchor = (
        '<a class="cover" data-platform='
        '"${escapeHtml(item.source_platform || item.platform || "bilibili")}"'
    )
    assert saved_anchor in app_js
    assert 'data-notification-msg="view" href="${escapeHtml(msg.content_url)}"' in app_js
    assert "function ensureDelightThumbAnchor()" in app_js
    assert 'document.createElement("a")' in app_js
    assert "function delightContentUrl(delight)" in app_js
    assert 'window.open(msg.content_url, "_blank", "noopener,noreferrer")' not in app_js


def test_cover_css_resets_anchor_defaults() -> None:
    app_css = APP_CSS.read_text(encoding="utf-8")

    cover_match = re.search(r"\.cover \{(?P<body>.*?)\}", app_css, flags=re.S)
    assert cover_match is not None, ".cover rule not found"
    cover_body = cover_match.group("body")

    assert "display: block;" in cover_body
    assert "text-decoration: none;" in cover_body
    assert "color: inherit;" in cover_body
