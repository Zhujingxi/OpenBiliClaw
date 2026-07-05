"""Static contract tests for the desktop web fatal-error banner source filter.

The global ``error`` / ``unhandledrejection`` listeners used to feed *every*
window error into the fatal banner — including ReferenceErrors thrown by
third-party browser extensions / userscripts injected into the page (real
user report: a userscript manager referencing ``userScripts`` produced a
scary 「页面脚本出现问题」 banner on a perfectly healthy page). The banner
must only fire for same-origin scripts; foreign errors go to the console.
"""

from __future__ import annotations

import re
from pathlib import Path

_APP_JS = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "openbiliclaw"
    / "web"
    / "desktop"
    / "assets"
    / "js"
    / "app.js"
).read_text(encoding="utf-8")


def _listener_body(event_name: str) -> str:
    marker = f'window.addEventListener("{event_name}"'
    assert marker in _APP_JS, f"missing global {event_name} listener"
    return _APP_JS.split(marker, 1)[1].split("});", 1)[0]


def test_error_listener_filters_foreign_scripts() -> None:
    body = _listener_body("error")
    assert "isForeignScriptError" in body, (
        "error listener must gate showFatal on the script-origin filter"
    )
    assert body.index("isForeignScriptError") < body.index("showFatal")


def test_rejection_listener_filters_foreign_scripts() -> None:
    body = _listener_body("unhandledrejection")
    assert "isForeignRejection" in body, (
        "unhandledrejection listener must gate showFatal on the origin filter"
    )
    assert body.index("isForeignRejection") < body.index("showFatal")


def test_foreign_filter_covers_extension_schemes_and_sanitized_errors() -> None:
    filter_fn = _APP_JS.split("function isForeignScriptError", 1)[1].split("\n    }", 1)[0]
    assert "window.location.origin" in filter_fn, "must compare against the page origin"
    assert re.search(r"if \(!filename\) return true", filter_fn), (
        "browser-sanitized cross-origin errors (empty filename) must count as foreign"
    )
    scheme_re = _APP_JS.split("FOREIGN_SCRIPT_URL_RE", 1)[1].split("\n", 1)[0]
    for scheme in ("chrome-extension", "moz-extension", "user-script"):
        assert scheme in scheme_re, f"rejection stack filter must recognise {scheme}:// frames"
