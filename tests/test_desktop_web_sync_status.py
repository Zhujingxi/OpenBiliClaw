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
    assert "last_account_sync_issues" in body


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
    assert 'severity === "warning"' in app_js

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
    assert "last_account_sync_issues" in render_body
    assert "last_account_sync_severity" in render_body
    assert "collectEnabledSourceIssues(state.sourceStatus)" in render_body
    assert "sourceIssues.map((issue) =>" in render_body
    assert "`${issue.source}：${issue.detail}`" in render_body
    assert 'classList.toggle("is-warning"' in render_body
    assert "（上次同步 ${when}）" in render_body
    assert "账号同步出错" not in render_body
    assert "未分类异常" in render_body
    # Timestamps go through the shared local-time formatter, not raw ISO.
    assert "formatLocalTime(" in render_body

    # Source diagnostics are loaded on the dashboard itself, not only after a
    # user opens settings; all eight platforms use the shared classifier and
    # backend detail rather than frontend-specific error copy.
    assert "function collectEnabledSourceIssues(data)" in app_js
    assert "SourceStatus.describeSourceIssue(data[key])" in app_js
    assert "SourceStatus.sourceLabel(key)" in app_js
    assert "renderAccountSyncStatus(state.runtimeStatus);" in app_js

    # normalizeRuntimeStatus() rebuilds the payload from an explicit key list,
    # so a field missing there is dropped before render. That is exactly how
    # the backend copy stopped reaching this chip once already — and how the
    # same field was lost on the Python side before that.
    normalize = re.search(
        r"function normalizeRuntimeStatus\((?P<args>[^)]*)\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert normalize is not None, "desktop normalizeRuntimeStatus not found"
    for field in (
        "last_account_sync_error_kind",
        "last_account_sync_issues",
        "last_account_sync_message",
        "last_account_sync_severity",
    ):
        assert field in normalize.group("body"), f"{field} dropped by the normalizer whitelist"

    assert 'id="accountSyncStatus"' in index_html
    assert ".account-sync-status" in app_css
    assert ".account-sync-status.is-warning" in app_css


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


def test_desktop_sync_error_chip_uses_readable_theme_foreground() -> None:
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    rule = re.search(
        r"\.account-sync-status\.is-error\s*\{(?P<body>.*?)\}",
        app_css,
        flags=re.S,
    )
    assert rule is not None, "desktop account sync error rule not found"
    body = rule.group("body")
    assert "color: var(--fg-2);" in body
    assert "background: color-mix(in oklab, var(--surface), var(--danger) 6%);" in body
    assert "color-mix(in oklab, var(--surface), var(--meta) 18%)" not in body
