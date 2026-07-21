"""Static acceptance contracts for the vNext web and extension cut-over."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPENAPI = ROOT / "openapi/openapi.json"
GENERATOR = ROOT / "openapi/generate-client.mjs"
WEB_CLIENT = ROOT / "src/openbiliclaw/web/js/api-client.js"
EXTENSION_CLIENT = ROOT / "extension/src/shared/api-client.ts"
POPUP_CLIENT = ROOT / "extension/popup/api-client.js"

DROPPED_EXTENSION_PATHS = (
    "extension/src/background/cookie-sync.ts",
    "extension/src/background/e2e-runner.ts",
    "extension/src/background/native-save-task-runner.ts",
    "extension/src/background/notifications.ts",
    "extension/src/content/e2e-executor.ts",
    "extension/src/main/xhs-token-sniffer.ts",
    "extension/src/shared/e2e.ts",
    "extension/src/shared/native-save.ts",
    "extension/popup/popup-model-settings.js",
    "extension/popup/popup-saved-sync.js",
    "extension/popup/popup-stream.js",
)

REQUIRED_OPERATIONS = {
    "v1_system_readiness",
    "v1_auth_status",
    "v1_auth_login",
    "v1_auth_logout",
    "v1_auth_extension_token",
    "v1_auth_revoke",
    "v1_system_ai_health",
    "v1_settings_get",
    "v1_settings_patch",
    "v1_onboarding_get",
    "v1_onboarding_start",
    "v1_onboarding_events",
    "v1_sources_list",
    "v1_sources_status",
    "v1_sources_configure_account",
    "v1_sources_disconnect_account",
    "v1_sources_get_settings",
    "v1_sources_update_settings",
    "v1_source_tasks_claim",
    "v1_source_tasks_complete",
    "v1_events_ingest",
    "v1_profile_get",
    "v1_profile_edit",
    "v1_feed_list",
    "v1_interactions_create",
    "v1_library_list",
    "v1_library_add",
    "v1_library_remove",
    "v1_chat_stream",
    "v1_chat_history",
    "v1_jobs_schedule",
    "v1_jobs_list",
    "v1_jobs_get",
    "v1_jobs_cancel",
    "v1_jobs_events",
}


def _operation_ids() -> set[str]:
    schema = json.loads(OPENAPI.read_text(encoding="utf-8"))
    return {
        operation["operationId"]
        for path in schema["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }


def test_generated_clients_are_deterministic_and_cover_retained_routes() -> None:
    assert _operation_ids() >= REQUIRED_OPERATIONS
    before = {
        WEB_CLIENT: WEB_CLIENT.read_bytes(),
        EXTENSION_CLIENT: EXTENSION_CLIENT.read_bytes(),
        POPUP_CLIENT: POPUP_CLIENT.read_bytes(),
    }
    subprocess.run(["node", str(GENERATOR), "--write"], cwd=ROOT, check=True)
    assert {path: path.read_bytes() for path in before} == before
    subprocess.run(["node", str(GENERATOR), "--check"], cwd=ROOT, check=True)
    for path in before:
        source = path.read_text(encoding="utf-8")
        assert {op for op in REQUIRED_OPERATIONS if op in source} >= REQUIRED_OPERATIONS


def test_active_web_surfaces_use_v1_clients_and_sse_without_legacy_controls() -> None:
    active = [
        ROOT / "src/openbiliclaw/web/setup/index.html",
        ROOT / "src/openbiliclaw/web/desktop/index.html",
        ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js",
        ROOT / "src/openbiliclaw/web/index.html",
        ROOT / "src/openbiliclaw/web/js/app.js",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in active)
    assert "api-client.js" in combined
    assert "/api/v1" in combined
    assert "EventSource" in combined or "readSse" in combined
    assert "WebSocket" not in combined
    for dropped in (
        "saved-sync",
        "savedAutoSync",
        "delightBanner",
        "modelEditorBoundary",
        "model-config",
        "updateApplyBtn",
        "OpenClaw",
        "desktop packaging",
    ):
        assert dropped not in combined
    for retained in ("favorites", "watch-later", "obc-interactive", "obc-analysis"):
        assert retained in combined


def test_popup_replaces_provider_editor_with_alias_health_and_litellm_admin() -> None:
    html = (ROOT / "extension/popup/popup.html").read_text(encoding="utf-8")
    script = (ROOT / "extension/popup/popup.js").read_text(encoding="utf-8")
    combined = html + script
    assert "/api/v1" in combined
    assert "api-client" in combined
    assert "LiteLLM Admin" in html
    assert "obc-interactive" in html
    assert "obc-analysis" in html
    assert "obc-embedding" in html
    for dropped in (
        "savedSync",
        "delightSlot",
        "model-config",
        "modelRoute",
        "backendUpdate",
        "notifications/pending",
    ):
        assert dropped not in combined


def test_active_extension_graph_keeps_capture_and_drops_native_mutation() -> None:
    service_worker = (ROOT / "extension/src/background/service-worker.ts").read_text(
        encoding="utf-8"
    )
    manifest = (ROOT / "extension/manifest.json").read_text(encoding="utf-8")
    active = service_worker + manifest
    assert "generic-source-task-dispatcher" in service_worker
    assert 'request("v1_events_ingest"' in service_worker
    assert "native-save" not in active
    assert "notifications" not in active
    assert "runtimeSocket" not in active
    for source in (
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
        "reddit",
    ):
        assert source in active


def test_dropped_extension_modules_are_physically_absent() -> None:
    for relative_path in DROPPED_EXTENSION_PATHS:
        assert not (ROOT / relative_path).exists(), relative_path
    legacy_dispatchers = {
        path.name for path in (ROOT / "extension/src/background").glob("*-task-dispatcher.ts")
    }
    assert legacy_dispatchers == {"generic-source-task-dispatcher.ts"}
    assert not list((ROOT / "extension/src/content/native-save").glob("*.ts"))
