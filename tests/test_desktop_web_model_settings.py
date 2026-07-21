"""Static contracts for the desktop ordered model-route editor."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "src/openbiliclaw/web/desktop"
INDEX = (DESKTOP / "index.html").read_text(encoding="utf-8")
APP_JS = (DESKTOP / "assets/js/app.js").read_text(encoding="utf-8")
CSS = (DESKTOP / "assets/css/app.css").read_text(encoding="utf-8")
MODEL_JS_PATH = DESKTOP / "assets/js/model-settings.js"
SHARED_STATE_PATH = ROOT / "src/openbiliclaw/web/shared/model-config-state.js"
API_APP = (ROOT / "src/openbiliclaw/api/app.py").read_text(encoding="utf-8")
EXTENSION_DOC = (ROOT / "docs/modules/extension.md").read_text(encoding="utf-8")


def test_model_page_has_three_route_tabs_and_list_inspector_landmarks() -> None:
    for route, label in (("chat", "Chat"), ("embedding", "Embedding"), ("runtime", "Runtime")):
        assert re.search(rf'data-model-route="{route}"[^>]*>[^<]*{label}', INDEX)

    for marker in (
        'id="modelRouteTabs"',
        'id="modelRouteListPane"',
        'id="modelRouteList"',
        'id="modelInspectorPane"',
        'id="modelInspector"',
        'aria-label="模型路由列表"',
        'aria-label="模型连接详情"',
    ):
        assert marker in INDEX


def test_connection_types_are_searchable_grouped_vertical_descriptors() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")
    shared_render = (
        Path(__file__).parents[1] / "src/openbiliclaw/web/shared/model-config-render.js"
    ).read_text(encoding="utf-8")

    assert 'id="modelTypeSearch"' in INDEX
    assert 'id="modelConnectionTypeGroups"' in INDEX
    assert 'role="listbox"' in INDEX
    assert "/api/model-connection-types" in model_js
    assert "descriptor.fields" in model_js
    assert "preset_definitions" in model_js
    # The grouped rendering now lives in the shared render module; the
    # desktop view keeps the keyboard wiring + change-type glue locally.
    assert "group.category" in shared_render
    assert "moveTypeOptionFocus" in model_js
    for key in ("ArrowUp", "ArrowDown", "Home", "End"):
        assert key in shared_render.split("export function moveTypeOptionFocus", 1)[1]
    assert 'addEventListener("keydown", moveTypeOptionFocus)' in model_js
    assert "function focusSelectedTypeOption" in model_js
    change_type = model_js.split("function changeType(typeId)", 1)[1].split(
        "function updateField", 1
    )[0]
    assert "focusSelectedTypeOption();" in change_type
    assert ".model-connection-type-groups" in CSS
    assert "grid-template-columns: 1fr" in CSS

    assert "data-connection-type-tab" not in INDEX
    assert 'id="llmProvider"' not in INDEX
    assert '<option value="openai">' not in INDEX


def test_deepseek_disabled_thinking_uses_an_empty_wire_value_on_every_web_surface() -> None:
    # The empty-string choice rendering for reasoning_effort moved into the
    # shared render module consumed by desktop + mobile; the wizard consumes
    # the shared escapeHtml primitive but keeps its own descriptor-field
    # markup (wizard layout uses div-wrapped .model-field instead of the
    # shared label-wrapped .settings-field — full adoption is plan §10), and
    # the extension popup carries the literal markup inline.
    surfaces = (
        (ROOT / "src/openbiliclaw/web/shared/model-config-render.js").read_text(encoding="utf-8"),
        (ROOT / "extension/popup/popup-model-settings.ts").read_text(encoding="utf-8"),
        (ROOT / "src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8"),
    )

    for source in surfaces:
        assert 'field.name === "reasoning_effort" && choice === "" ? "disabled"' in source

    # Desktop and mobile route through the shared renderer, so the literal
    # string must NOT be forked into their per-surface modules.
    for fork in (
        MODEL_JS_PATH.read_text(encoding="utf-8"),
        (ROOT / "src/openbiliclaw/web/js/views/model-settings.js").read_text(encoding="utf-8"),
    ):
        assert "sharedRenderDescriptorField" in fork or "renderDescriptorField" in fork


def test_route_rows_support_drag_buttons_keyboard_and_focus_restoration() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")

    assert "draggable" in model_js
    assert "model-route-drag-handle" in model_js
    assert ">上移</button>" in INDEX
    assert ">下移</button>" in INDEX
    assert "Move Up" not in INDEX
    assert "Move Down" not in INDEX
    assert "ArrowUp" in model_js
    assert "ArrowDown" in model_js
    assert ".focus()" in model_js


def test_visible_model_editor_copy_is_chinese_first_and_keeps_technical_terms() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")
    shared_render = (
        Path(__file__).parents[1] / "src/openbiliclaw/web/shared/model-config-render.js"
    ).read_text(encoding="utf-8")
    # After the shared-render extraction the copy lives in either the
    # desktop module (route list, drag handles) or the shared render module
    # (descriptor fields, credential editor, category labels).
    combined = model_js + "\n" + shared_render

    for copy in (
        'aria-label="拖拽排序"',
        '"顺序由只读覆盖配置提供"',
        '"未命名连接"',
        '"未设置模型"',
        '"API 协议"',
        '"本地 Runtime"',
        '"OAuth 连接"',
        "<strong>已导入 OAuth 凭据</strong>",
        '"保留现有凭据"',
        '"设置 API Key"',
        '"当前未配置凭据。"',
        "<span>稳定 ID</span>",
        "<span>Chat 路由</span>",
        "<span>Embedding 路由</span>",
        "<span>当前健康状态</span>",
    ):
        assert copy in combined
    for old_copy in (
        "Drag to reorder",
        "Unnamed connection",
        "No model",
        "API protocols",
        "Local runtimes",
        "OAuth connections",
        "Imported OAuth credential",
        "Keep existing",
        "Credential source",
        "Current health",
        "Probe failed",
    ):
        assert old_copy not in combined


def test_legacy_provider_fallback_and_module_override_fields_are_absent() -> None:
    for legacy_id in (
        "llmProvider",
        "llmFallbackProvider",
        "embeddingProvider",
        "embeddingFallbackProvider",
        "moduleSoulProvider",
        "moduleSoulModel",
        "moduleDiscoveryProvider",
        "moduleDiscoveryModel",
        "moduleRecommendationProvider",
        "moduleRecommendationModel",
        "moduleEvaluationProvider",
        "moduleEvaluationModel",
    ):
        assert f'id="{legacy_id}"' not in INDEX
        assert f"#{legacy_id}" not in APP_JS


def test_narrow_model_layout_switches_from_list_to_detail() -> None:
    assert "@media (max-width: 820px)" in CSS
    assert "@container desktop-main (max-width: 940px)" in CSS
    assert ".model-route-layout.is-detail" in CSS
    assert ".model-route-list-pane" in CSS
    assert ".model-inspector-pane" in CSS
    assert 'id="modelInspectorBack"' in INDEX
    assert "data-model-view" in INDEX


def test_narrow_detail_moves_focus_and_back_restores_the_selected_row_control() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")

    assert 'matchMedia("(max-width: 820px)")' in model_js
    assert "layout.getBoundingClientRect().width <= 940" in model_js
    assert "function focusNarrowDetail" in model_js
    assert 'byId("modelInspectorBack")?.focus()' in model_js
    assert "function focusSelectedRouteControl" in model_js
    assert "[data-model-select=" in model_js
    assert "focusSelectedRouteControl();" in model_js
    assert 'byId("modelRouteListPane").focus' not in model_js


def test_settings_layout_is_wide_dense_and_keeps_descriptor_copy_separate() -> None:
    assert ".settings-page { max-width: 1480px; font-size: 14px; }" in CSS
    assert ".settings-page .content-page-head h2" in CSS
    assert ".settings-panel:not(.model-settings-panel)" in CSS
    assert ".model-type-option > span:first-child" in CSS
    assert ".model-type-option > small" in CSS
    assert "@container desktop-main (max-width: 720px)" in CSS
    assert ".model-save-bar .settings-probe-status { flex: 0 1 auto; }" in CSS


def test_route_empty_state_is_compact_localized_and_kind_specific() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")

    for copy in (
        '"调用顺序"',
        '"Chat 路由"',
        '"Embedding 路由"',
        '"添加 Provider"',
        '"尚未添加 Chat 连接。"',
        '"尚未添加 Embedding Provider。"',
    ):
        assert copy in model_js
    assert "当前 route 为空。" not in model_js
    assert ".model-route-list-pane #modelAddConnection" in CSS
    assert ".model-route-empty" in CSS


def test_model_and_general_saves_have_separate_owners() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")
    build_update = APP_JS.split("function buildConfigUpdate()", 1)[1].split(
        "function configErrorMessage", 1
    )[0]

    assert 'id="modelSaveButton"' in INDEX
    assert 'id="generalSettingsActions"' in INDEX
    assert "/api/model-config" in model_js
    assert 'method: "PUT"' in model_js
    assert "toModelConfigPayload" in model_js
    assert "llm" not in build_update
    assert 'data-settings-panel="models"' in INDEX
    assert "generalSettingsActions" in APP_JS


def test_model_save_locks_the_complete_editor_for_one_guarded_transaction() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")
    save = model_js.split("async function saveModels()", 1)[1].split(
        "async function fetchModelSnapshot", 1
    )[0]
    render = model_js.split("function render()", 1)[1].split("function focusMovedRow", 1)[0]

    assert 'id="modelEditorBoundary"' in INDEX
    assert "let saveInFlight = false;" in model_js
    assert "let saveGeneration = 0;" in model_js
    assert "function setModelEditorLocked" in model_js
    assert "function modelMutationBlocked" in model_js
    assert model_js.count("modelMutationBlocked()") >= 10
    assert "if (!state || saveInFlight) return;" in save
    assert "const generation = ++saveGeneration;" in save
    assert "setModelEditorLocked(true);" in save
    assert "generation === saveGeneration" in save
    assert "setModelEditorLocked(false);" in save
    assert 'byId("modelSaveButton").disabled = saveInFlight;' in render
    reloaded = model_js.split('window.addEventListener("openbiliclaw:config-reloaded"', 1)[1].split(
        "});", 1
    )[0]
    assert "saveInFlight" in reloaded


def test_model_save_invalidates_already_started_snapshot_reloads() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")
    save = model_js.split("async function saveModels()", 1)[1].split(
        "async function fetchModelSnapshot", 1
    )[0]
    fetch_snapshot = model_js.split("async function fetchModelSnapshot", 1)[1].split(
        "async function loadModelSettings", 1
    )[0]

    assert "createLatestRequestGate" in model_js
    assert "applyLatestSnapshotRequest" in model_js
    assert "snapshotRequestGate.invalidate();" in save
    assert "blocked: () => saveInFlight" in fetch_snapshot


def test_initial_snapshot_load_uses_the_same_latest_request_gate_as_reload() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")
    initial_load = model_js.split("async function loadModelSettings()", 1)[1].split(
        "function confirmLeave", 1
    )[0]

    assert "applyLatestSnapshotRequest" in initial_load
    assert "gate: snapshotRequestGate" in initial_load
    assert "blocked: () => saveInFlight" in initial_load
    assert "let descriptorsReady;" in initial_load
    assert "Promise.all([snapshotLoad, descriptorsReady])" in initial_load
    assert "connectionTypes = descriptors;" in initial_load
    assert "if (state && !saveInFlight) render();" in initial_load


def test_model_editor_owns_dirty_remote_probe_error_and_migration_lifecycle() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")

    for marker in (
        "beforeunload",
        "config_reloaded",
        "receiveRemoteSnapshot",
        "mapServerFieldErrors",
        "migration_resolutions",
        "/api/model-config/probe",
        "observed_dimension",
        "probed_at",
        "credential.action",
        "revision_conflict",
    ):
        assert marker in model_js


def test_probe_revision_conflict_renders_the_replaced_or_retained_state_completely() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")
    probe = model_js.split("async function probeSelected()", 1)[1].split(
        "function retainSelection", 1
    )[0]
    conflict = probe.split("if (error.status === 409 && error.details?.latest)", 1)[1]

    assert "state = receiveRemoteSnapshot(state, error.details.latest);" in conflict
    assert "render();" in conflict.split("}", 1)[0]
    assert "renderRemoteUpdate();" not in conflict.split("}", 1)[0]


def test_probe_completion_is_generation_guarded_and_scoped_to_its_stable_record() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")
    probe = model_js.split("async function probeSelected()", 1)[1].split(
        "function retainSelection", 1
    )[0]

    assert "let probeGeneration = 0;" in model_js
    for marker in (
        "createProbeSignature",
        "applyProbeResult",
        "probeSignatureMatches",
        "const generation = ++probeGeneration;",
        "generation !== probeGeneration",
        "probeRequestVisible(signature)",
    ):
        assert marker in probe
    assert "record.probe =" not in probe


def test_local_model_overrides_are_visible_and_lock_only_shadowed_controls() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")

    assert 'id="modelOverrideNotice"' in INDEX
    assert "function renderOverrides" in model_js
    assert "state.overrideLocks" in model_js
    assert "override.source" in model_js
    assert "override.path" in model_js
    assert "function routeLocked" in model_js
    assert "function modelControlLocked" in model_js
    for path in (
        "models.chat.connections",
        "models.chat.concurrency",
        "models.chat.timeout_seconds",
        "models.embedding.enabled",
        "models.embedding.settings.model",
        "models.embedding.settings.output_dimensionality",
        "models.embedding.settings.similarity_threshold",
        "models.embedding.settings.multimodal_enabled",
        "models.embedding.providers",
    ):
        assert path in model_js
    assert "routeLocked(state.activeRoute)" in model_js
    assert "modelControlLocked(" in model_js
    assert ".disabled = Boolean(" in model_js
    assert "disabledMarkup" in model_js


def test_model_modules_are_loaded_and_cache_busted_by_the_backend() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")

    assert 'type="module" src="/web/assets/js/model-settings.js"' in INDEX
    assert "import.meta.url" in model_js
    assert 'searchParams.get("v")' in model_js
    assert 'searchParams.set("v"' in model_js
    assert "await import(" in model_js
    assert 'from "/web/shared/model-config-state.js"' not in model_js
    assert '"assets/js/model-settings.js"' in API_APP
    assert '"shared/model-config-state.js"' in API_APP
    assert re.search(r'app\.mount\(\s*"/web/shared"', API_APP)


def test_extension_documentation_describes_native_popup_and_desktop_editors() -> None:
    stale_desktop_claim = (
        "桌面 Web（`/web`）设置页 `src/openbiliclaw/web/desktop/` 的可配置面"
        "与插件 side panel 拉齐：模型 tab 补 `llm.concurrency`"
    )

    assert "模型 tab 以 Chat / Embedding / Runtime 次级 tab" in EXTENSION_DOC
    assert "插件在 popup 宽度使用列表→详情顺序流" in EXTENSION_DOC
    assert "桌面 Web 使用宽屏列表 + 侧边 inspector" in EXTENSION_DOC
    assert "revisioned `PUT /api/model-config`" in EXTENSION_DOC
    assert "插件 side panel 仍展示 legacy 默认/备选 Provider" not in EXTENSION_DOC
    assert "插件 side panel 的 legacy 模型 tab" not in EXTENSION_DOC
    assert "Task 11" not in EXTENSION_DOC
    assert "v0.3.157+ 与桌面 Web 对齐" not in EXTENSION_DOC
    assert stale_desktop_claim not in EXTENSION_DOC
