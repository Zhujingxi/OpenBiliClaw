import re
from pathlib import Path


def test_desktop_normalizes_account_sync_error_fields() -> None:
    """Runtime status normalization must carry the account-sync error fields."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    normalize = re.search(
        r"function normalizeRuntimeStatus\(status\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert normalize is not None, "desktop normalizeRuntimeStatus not found"
    body = normalize.group("body")
    assert "last_account_sync_error" in body
    assert "last_account_sync_error_kind" in body


def test_desktop_renders_account_sync_error_chip() -> None:
    """Sync failures (esp. auth-expired) render an actionable status chip."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")
    index_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")

    assert "function renderAccountSyncStatus(" in app_js
    assert "renderAccountSyncStatus(" in app_js
    # auth-expired is a distinguishable, re-login-needed state. The literal is
    # only a fallback now — the backend renders the sentence so every surface
    # says the same thing — but the branch must still exist.
    assert "B 站登录已失效，账号同步已停止 — 请重新登录" in app_js
    assert 'kind === "auth_expired"' in app_js
    assert "last_account_sync_message" in app_js

    render = re.search(
        r"function renderAccountSyncStatus\((?P<args>[^)]*)\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert render is not None, "desktop renderAccountSyncStatus not found"
    render_body = render.group("body")
    # muted chip for generic errors carries the sync time, and the raw provider
    # error stays available for diagnostics even though it is no longer shown.
    assert "last_account_sync_error" in render_body
    assert "last_account_sync_at" in render_body
    # Timestamps go through the shared local-time formatter, not raw ISO.
    assert "formatLocalTime(" in render_body

    assert 'id="accountSyncStatus"' in index_html
    assert ".account-sync-status" in app_css


def test_desktop_apply_runtime_status_renders_sync_chip() -> None:
    """applyRuntimeStatus must drive the account-sync status chip."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    apply_fn = re.search(
        r"function applyRuntimeStatus\(payload\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert apply_fn is not None, "desktop applyRuntimeStatus not found"
    assert "renderAccountSyncStatus(" in apply_fn.group("body")
