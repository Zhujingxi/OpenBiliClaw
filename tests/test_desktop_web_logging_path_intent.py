"""Static regressions for the desktop settings logging-path intent tracking.

The ``GET /api/config`` logging fields are redacted, so the desktop settings
editor must distinguish an *unchanged* full-form save (send the ``file_path``
echo back so the backend preserves canonical absolute paths) from an
*intentional* edit — including an edit whose final value equals the exact
displayed basename. Final string equality cannot express that distinction, so
the client tracks an explicit dirty flag armed by real ``input`` events.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js"


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_desktop_web_log_path_has_explicit_dirty_flag_helpers() -> None:
    js = _js()

    assert "let logPathDirty = false;" in js
    assert "function markLogPathDirty()" in js
    assert "logPathDirty = true;" in js
    assert "function resetLogPathDirty()" in js
    assert "logPathDirty = false;" in js


def test_desktop_web_log_path_classifier_consults_dirty_flag_before_value() -> None:
    """``isLogPathUnmodified`` must return False whenever the dirty flag is
    armed, even if the final input value equals the redacted GET echo (the
    edit-away-then-back / exact-basename revert case)."""
    js = _js()
    classifier = js.split("function isLogPathUnmodified", 1)[1]

    assert "if (logPathDirty) return false;" in classifier


def test_desktop_web_log_path_dirty_flag_armed_by_user_input_event() -> None:
    """The dirty flag must be armed by a real ``input`` event on the field —
    programmatic ``setInput`` renders do not fire ``input``, so only genuine
    user edits mark the field dirty."""
    js = _js()

    assert 'safeBind("#logPath", "input", () => markLogPathDirty());' in js


def test_desktop_web_log_path_dirty_flag_reset_on_config_render() -> None:
    """Rendering the backend config into the form (initial load, hot reload,
    post-save rehydration) must reset the flag: the field is pristine again."""
    js = _js()
    render_block = js.split('setInput("logPath", resolveLogPath(config.logging));', 1)[1]

    assert "resetLogPathDirty();" in render_block[:400]


def test_desktop_web_log_path_payload_branch_unchanged() -> None:
    """The save payload must still send ``file_path`` only for pristine
    echoes and ``directory``/``filename`` for intentional edits."""
    js = _js()

    assert "if (isLogPathUnmodified(state.config?.logging)) {" in js
    assert 'base.file_path = getInput("logPath");' in js
    assert "base.directory = logPath.directory;" in js
    assert "base.filename = logPath.filename;" in js
