from pathlib import Path


APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")


def test_desktop_normalizers_carry_publication_fields() -> None:
    assert 'published_at: String(item?.published_at ?? "")' in APP_JS
    assert 'published_label: String(item?.published_label ?? "")' in APP_JS


def test_desktop_formats_and_renders_publication_time_on_grid_and_delight() -> None:
    assert "function formatPublishedTime(item, now = Date.now())" in APP_JS
    assert "const published = formatPublishedTime(item);" in APP_JS
    assert 'class="published-time"' in APP_JS
    assert 'id="delightPublished"' in INDEX_HTML
    assert ".published-time" in APP_CSS
