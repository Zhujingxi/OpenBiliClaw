import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    match = re.search(rf"function {name}\([^)]*\) \{{(?P<body>.*?)\n    \}}", APP_JS, flags=re.S)
    assert match is not None, f"{name} function not found"
    return match.group("body")


def test_delight_view_model_keeps_decoded_body_text() -> None:
    normalize = _function_body("normalizeDelight")
    assert "body_text: delightBody" in normalize


def test_delight_excerpt_has_accessible_expand_controls() -> None:
    assert 'id="delightExcerpt"' in INDEX_HTML
    assert 'id="delightExcerptText"' in INDEX_HTML
    assert 'id="delightExcerptToggle"' in INDEX_HTML
    assert 'aria-controls="delightExcerptText"' in INDEX_HTML
    assert 'aria-expanded="false"' in INDEX_HTML

    sync = _function_body("syncDelightExcerpt")
    assert ".textContent = bodyText" in sync
    assert "excerpt.scrollHeight > excerpt.clientHeight + 1" in sync
    assert "toggle.hidden = !overflows" in sync
    assert 'toggle.setAttribute("aria-expanded", "false")' in sync


def test_delight_excerpt_css_clamps_five_lines_until_expanded() -> None:
    assert ".delight-excerpt-text" in APP_CSS
    assert "-webkit-line-clamp: 5" in APP_CSS
    assert ".delight-excerpt.is-expanded .delight-excerpt-text" in APP_CSS
    assert "-webkit-line-clamp: unset" in APP_CSS


def test_missing_or_failed_delight_cover_uses_text_media_fallback() -> None:
    render_cover = _function_body("renderDelightCover")
    render_text = _function_body("renderDelightTextMedia")
    render_fallback = _function_body("renderDelightFallbackMedia")
    assert "renderDelightFallbackMedia(thumb, delight)" in render_cover
    assert 'image.addEventListener("error"' in render_cover
    assert "image.parentElement !== thumb" in render_cover
    assert "renderDelightTextMedia(thumb, delight)" in render_fallback
    assert 'String(delight?.body_text || "").trim()' in render_fallback
    assert 'text.className = "delight-text-media-copy"' in render_text
    assert "text.textContent = bodyText" in render_text
    assert 'thumb.classList.add("is-text-media")' in render_text
    assert "thumb.dataset.platform" in render_text
    assert ".delight .thumb.is-text-media" in APP_CSS
    assert ".delight-text-media-copy" in APP_CSS
    assert '.delight .thumb.is-text-media[data-platform="zhihu"]' in APP_CSS
