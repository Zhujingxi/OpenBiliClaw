"""Source-inspection guards for the probe "暂时忽略" (defer) desktop UI."""

import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")


def test_message_card_renders_defer_button_between_confirm_and_reject() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    # Icon button in the message-card renderer, gray is-neutral styling.
    assert 'data-probe="defer"' in app_js
    assert 'class="feedback-icon-btn is-neutral"' in app_js
    # Order: confirm ... defer ... reject within the card feedback icons.
    confirm = app_js.index('data-probe="confirm"')
    defer = app_js.index('data-probe="defer"')
    reject = app_js.index('data-probe="reject"')
    assert confirm < defer < reject


def test_profile_speculation_row_has_defer_button() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    assert 'data-spec-response="defer"' in app_js
    assert 'class="probe-btn is-neutral"' in app_js
    confirm = app_js.index('data-spec-response="confirm"')
    defer = app_js.index('data-spec-response="defer"')
    reject = app_js.index('data-spec-response="reject"')
    assert confirm < defer < reject


def test_defer_copy_is_honest_not_permanent() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    # Deferred copy promises the probe may return; never "已忽略"-as-permanent.
    assert "过阵子可能再提" in app_js
    # Exhaustion copy keys off the API's defer_exhausted action.
    assert 'apiResp?.action === "defer_exhausted"' in app_js
    assert "之后先不提" in app_js


def test_deferred_events_do_not_trigger_profile_refresh() -> None:
    """defer does not mutate the profile, so interest.deferred /
    avoidance.deferred must NOT be in the profile-refresh branch. The
    unconditional applyRuntimeStatus already surfaces the live summary."""
    app_js = APP_JS.read_text(encoding="utf-8")
    refresh = re.search(
        r"if \(\s*\n?\s*event\.type === \"profile_updated\""
        r"(?P<body>.*?)\) void refreshProfile\(\);",
        app_js,
        flags=re.S,
    )
    assert refresh is not None, "profile-refresh branch not found"
    body = refresh.group("body")
    assert "interest.deferred" not in body
    assert "avoidance.deferred" not in body


def test_defer_button_css_present() -> None:
    app_css = APP_CSS.read_text(encoding="utf-8")
    assert ".feedback-icon-btn.is-neutral" in app_css
    assert ".spec-actions .probe-btn.is-neutral" in app_css
