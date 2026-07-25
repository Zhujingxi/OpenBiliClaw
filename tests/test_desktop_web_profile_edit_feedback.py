import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")


def _function_body(js: str, name: str) -> str:
    match = re.search(
        rf"function {name}\([^)]*\) \{{(?P<body>.*?)\n    \}}",
        js,
        flags=re.S,
    )
    assert match is not None, f"{name} function not found"
    return match.group("body")


def _listener_body(bind_body: str, selector: str) -> str:
    escaped_selector = re.escape(selector)
    match = re.search(
        rf'querySelectorAll\("{escaped_selector}"\).*?addEventListener\("click", async \(\) => '
        r"\{(?P<body>.*?)\n      \}\);",
        bind_body,
        flags=re.S,
    )
    assert match is not None, f"{selector} async click listener not found"
    return match.group("body")


def test_edit_chip_pending_style_is_declared() -> None:
    app_css = APP_CSS.read_text(encoding="utf-8")

    assert ".edit-chip.is-pending { opacity: .45; pointer-events: none; }" in app_css


def test_remove_chips_mark_pending_before_profile_edit_request() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    bind_body = _function_body(app_js, "bindProfileEditActions")

    for selector in ("[data-edit-remove]", "[data-edit-remove-specific]"):
        listener = _listener_body(bind_body, selector)
        guard_idx = listener.index('chip?.classList.contains("is-pending")')
        pending_idx = listener.index('chip?.classList.add("is-pending")')
        disabled_idx = listener.index("btn.disabled = true;")
        await_idx = listener.index("await applyProfileEdit(")

        assert guard_idx < pending_idx
        assert pending_idx < await_idx
        assert disabled_idx < await_idx


def test_add_buttons_disable_before_profile_edit_request() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    bind_body = _function_body(app_js, "bindProfileEditActions")

    for selector in ("[data-edit-add]", "[data-edit-add-specific]"):
        listener = _listener_body(bind_body, selector)
        guard_idx = listener.index("if (btn.disabled) return;")
        disabled_idx = listener.index("btn.disabled = true;")
        await_idx = listener.index("await applyProfileEdit(")

        assert guard_idx < disabled_idx
        assert disabled_idx < await_idx
