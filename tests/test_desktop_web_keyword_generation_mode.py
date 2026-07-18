"""Static regressions for the desktop-web keyword-generation-mode selector."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_HTML = ROOT / "src/openbiliclaw/web/desktop/index.html"
_JS = ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js"
_FRONTEND_ROOTS = (
    ROOT / "src/openbiliclaw/web/js",
    ROOT / "src/openbiliclaw/web/desktop",
    ROOT / "src/openbiliclaw/web/setup",
    ROOT / "extension/popup",
    ROOT / "extension/src",
)


def test_desktop_web_html_wires_keyword_generation_mode_select() -> None:
    html = _HTML.read_text(encoding="utf-8")
    assert 'id="keywordGenerationMode"' in html
    # Three options with the exact backend-facing values + Chinese labels.
    assert '<option value="legacy">经典</option>' in html
    assert '<option value="hybrid">混合</option>' in html
    assert '<option value="inspiration">灵感</option>' in html
    # A cost hint explaining hybrid is the most expensive mode.
    assert "settings-note-inline" in html
    assert "混合最贵" in html


def test_desktop_web_js_loads_keyword_generation_mode() -> None:
    js = _JS.read_text(encoding="utf-8")
    assert 'setSelect("keywordGenerationMode", discovery.keyword_generation_mode || "legacy")' in js


def test_desktop_web_js_saves_keyword_generation_mode_after_spread() -> None:
    js = _JS.read_text(encoding="utf-8")
    save_key = 'keyword_generation_mode: $("#keywordGenerationMode").value'
    assert save_key in js
    # Spread-order gotcha (R2): the key must come AFTER the discovery snapshot
    # spread, or the loaded value would clobber the user's selection.
    spread = "...(state.config?.discovery || {})"
    assert js.index(spread) < js.index(save_key)


@pytest.mark.parametrize("value", ["legacy", "hybrid", "inspiration"])
def test_desktop_web_option_values_match_backend(value: str) -> None:
    html = _HTML.read_text(encoding="utf-8")
    assert f'<option value="{value}">' in html


def test_frontend_has_no_removed_inspiration_config_controls() -> None:
    inspiration_keys: set[str] = set()
    for root in _FRONTEND_ROOTS:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".css", ".html", ".js", ".json", ".mjs", ".ts"}:
                inspiration_keys.update(
                    re.findall(r"\binspiration_[a-z0-9_]+\b", path.read_text(encoding="utf-8"))
                )

    assert inspiration_keys <= {"inspiration_breadth"}
