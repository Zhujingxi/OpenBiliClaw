import re
from pathlib import Path

APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\}}", APP_CSS, flags=re.S)
    assert match is not None, f"CSS rule not found: {selector}"
    return match.group("body")


def test_delight_text_media_uses_theme_foreground_over_surface_tints() -> None:
    base = _rule(".delight .thumb.is-text-media")
    copy = _rule(".delight-text-media-copy")

    assert "background: var(--surface);" in base
    assert "border: 1px solid var(--border-soft);" in base
    assert "color: var(--fg);" in base
    assert "color: var(--fg);" in copy
    assert "color: var(--fg-2);" not in copy

    for platform in ("zhihu", "bilibili", "reddit"):
        platform_rule = _rule(f'.delight .thumb.is-text-media[data-platform="{platform}"]')
        assert "background: color-mix(in oklab, var(--surface)," in platform_rule
        assert " 8%);" in platform_rule


def test_desktop_coverless_text_cards_keep_opaque_theme_surface() -> None:
    cover = _rule(".cover.is-text-card:not(.has-backdrop)")
    cover_text = _rule(".cover.is-text-card:not(.has-backdrop) .cover-text")

    assert "background: var(--surface);" in cover
    assert "border: 1px solid var(--border-soft);" in cover
    assert "color: var(--fg);" in cover_text
    assert "text-shadow: none;" in cover_text


def test_classic_theme_does_not_restore_low_contrast_text_media_gradient() -> None:
    delight = _rule(':root[data-accent="classic"] .delight .thumb.is-text-media')
    delight_scrim = _rule(':root[data-accent="classic"] .delight .thumb.is-text-media::before')
    cover = _rule(':root[data-accent="classic"] .cover.is-text-card:not(.has-backdrop)')

    assert "background: var(--surface);" in delight
    assert "border-color: var(--border-soft);" in delight
    assert "linear-gradient" not in delight
    assert "background: color-mix(in oklab, var(--surface), transparent 8%);" in delight_scrim
    assert "background: var(--surface);" in cover
