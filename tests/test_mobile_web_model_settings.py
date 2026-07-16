"""Static contracts for the mobile ordered model-route editor."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/openbiliclaw/web"
APP_PATH = WEB / "js/app.js"
API_PATH = WEB / "js/api.js"
MODEL_PATH = WEB / "js/views/model-settings.js"
CSS_PATH = WEB / "css/app.css"
FOCUS_RUNTIME_PATH = WEB / "js/saved-sync-runtime.js"
CONTROLLER_PATH = WEB / "js/mobile-model-settings-controller.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_mobile_model_api_uses_same_origin_bounded_requests_without_key_reveal() -> None:
    api = _read(API_PATH)

    for name in (
        "fetchModelConfig",
        "fetchModelConnectionTypes",
        "updateModelConfig",
        "probeModelConnection",
    ):
        assert f"export async function {name}" in api
    assert 'requestJson("/model-config"' in api
    assert 'requestJson("/model-connection-types"' in api
    assert 'requestJson("/model-config/probe"' in api
    assert "MODEL_WRITE_TIMEOUT_MS = 60_000" in api
    model_api = api.split("// ── Model configuration", 1)[1]
    assert "reveal_keys" not in model_api


def test_mobile_shell_delegates_settings_to_the_authoritative_view_module() -> None:
    app = _read(APP_PATH)

    assert 'from "./views/model-settings.js"' in app
    assert "openMobileSettings" in app
    assert "function openMobileSettings(" not in app
    assert "openbiliclaw:config-reloaded" in app
    assert "new CustomEvent" in app


def test_settings_overlay_keeps_saved_sync_and_adds_a_models_section() -> None:
    model = _read(MODEL_PATH)

    for marker in (
        'data-mobile-settings-section="saved"',
        'data-mobile-settings-section="models"',
        'data-mobile-settings-panel="saved"',
        'data-mobile-settings-panel="models"',
        'id="mobile-saved-auto-sync"',
        "fetchConfig",
        "updateConfig",
    ):
        assert marker in model


def test_saved_sync_update_can_never_forward_legacy_or_native_model_objects() -> None:
    model = _read(MODEL_PATH)

    assert "function buildSavedSyncUpdate(" in model
    payload = model.split("function buildSavedSyncUpdate(", 1)[1].split("}", 2)[0]
    assert "saved_sync" in payload
    assert "llm" not in payload
    assert "models" not in payload
    saved_save = model.split("async function saveSavedSync()", 1)[1].split(
        "function activeItems", 1
    )[0]
    assert "updateConfig(buildSavedSyncUpdate(" in saved_save


def test_models_use_chat_embedding_runtime_tabs_and_sequential_list_detail() -> None:
    model = _read(MODEL_PATH)

    for route, label in (("chat", "Chat"), ("embedding", "Embedding"), ("runtime", "Runtime")):
        assert re.search(rf'data-mobile-model-route="{route}"[^>]*>[^<]*{label}', model)
    for marker in (
        'id="mobileModelRouteList"',
        'id="mobileModelInspector"',
        'id="mobileModelInspectorBack"',
        "is-detail",
        "selectRecord",
        "showRouteList",
    ):
        assert marker in model


def test_mobile_editor_imports_and_uses_the_shared_desktop_reducer() -> None:
    model = _read(MODEL_PATH)

    assert 'from "../../shared/model-config-state.js"' in model
    for reducer in (
        "appendRouteItem",
        "removeRouteItem",
        "moveRouteItem",
        "selectRouteItem",
        "updateRouteField",
        "updateRouteSetting",
        "toModelConfigPayload",
    ):
        assert reducer in model
    assert "items.length >= MAX_ROUTE_ITEMS" in model


def test_mobile_routes_add_remove_and_touch_reorder_by_stable_id() -> None:
    model = _read(MODEL_PATH)

    for marker in (
        "function addConnection()",
        "function removeSelected()",
        "function moveSelected(delta)",
        "Move Up",
        "Move Down",
        "record.id",
        "derivedRole(index)",
    ):
        assert marker in model
    assert "draggable" not in model


def test_connection_types_are_grouped_searchable_and_descriptor_driven() -> None:
    model = _read(MODEL_PATH)

    for marker in (
        'id="mobileModelTypeSearch"',
        'role="listbox"',
        "connectionTypes.groups",
        "group.category",
        "descriptor.fields",
        "preset_definitions",
        "field.capabilities",
        "field.presets",
        "function moveTypeOptionFocus",
    ):
        assert marker in model


def test_credentials_and_embedding_shared_settings_keep_the_full_contract() -> None:
    model = _read(MODEL_PATH)

    for action in ("keep", "set", "env", "clear"):
        assert f'["{action}",' in model
    for marker in (
        "credential.action",
        "credential.status",
        'id="mobileModelEmbeddingModel"',
        'id="mobileModelEmbeddingDimension"',
        'id="mobileModelEmbeddingSimilarity"',
        'id="mobileModelEmbeddingMultimodal"',
        "settings.output_dimensionality",
        "settings.similarity_threshold",
        "settings.multimodal_enabled",
    ):
        assert marker in model


def test_runtime_override_migration_and_field_errors_are_rendered() -> None:
    model = _read(MODEL_PATH)

    for marker in (
        'id="mobileModelChatConcurrency"',
        'id="mobileModelChatTimeout"',
        "function renderOverrides",
        "state.overrideLocks",
        "override.source",
        "override.path",
        "function renderMigration",
        "migration_resolutions",
        "mapServerFieldErrors",
        "connection_id",
    ):
        assert marker in model


def test_exact_probe_is_revisioned_generation_guarded_and_fingerprinted() -> None:
    model = _read(MODEL_PATH)
    probe = model.split("async function probeSelected()", 1)[1].split(
        "function retainSelection", 1
    )[0]

    for marker in (
        "beginProbe",
        "createProbeSignature",
        "applyProbeResult",
        "probeSignatureMatches",
        "signature.revision",
        "signature.kind",
        "signature.id",
        "signature.fingerprint",
        "probeModelConnection",
        "observed_dimension",
        "probed_at",
    ):
        assert marker in probe
    assert "record.probe =" not in probe
    assert probe.index("numericValidation.runProbeIfValid") < probe.index("createProbeSignature")
    assert probe.index("numericValidation.runProbeIfValid") < probe.index("toModelConfigPayload")


def test_revisioned_save_locks_the_editor_and_keeps_safe_failure_drafts() -> None:
    model = _read(MODEL_PATH)
    save = model.split("async function saveModels()", 1)[1].split(
        "async function fetchModelSnapshot", 1
    )[0]
    for marker in (
        "beginSave",
        "modelResources.invalidateSnapshotRequests()",
        "setModelEditorLocked(true)",
        "updateModelConfig(toModelConfigPayload(state))",
        "revision_conflict",
        "receiveRemoteSnapshot",
        "mapServerFieldErrors",
        "finishSave",
        "setModelEditorLocked(false)",
    ):
        assert marker in save
    assert "hydrateModelConfig(error.details.latest)" not in save


def test_save_invalidates_probe_and_resets_a_pending_probe_label() -> None:
    model = _read(MODEL_PATH)
    save = model.split("async function saveModels()", 1)[1].split(
        "async function fetchModelSnapshot", 1
    )[0]

    assert "save.invalidatedProbe" in save
    assert "renderProbeStatus" in save
    assert "正在探测精确草稿" in model
    assert "尚未探测此精确草稿" in model


def test_latest_snapshot_and_descriptor_ownership_are_rechecked_after_settle() -> None:
    model = _read(MODEL_PATH)
    controller = _read(CONTROLLER_PATH)
    load = model.split("async function loadModelSettings()", 1)[1].split(
        "function confirmLeave", 1
    )[0]

    assert "createMobileModelResourceCoordinator" in model
    assert "modelResources.enterModels()" in load
    assert "createLatestRequestGate" in controller
    assert "loadIndependentModelResources" in controller
    assert "snapshotReady" in controller
    assert "descriptorsReady" in controller
    assert "preserveStatus" in model


def test_settled_incomplete_mobile_load_uses_the_recovery_controller() -> None:
    model = _read(MODEL_PATH)
    controller = _read(CONTROLLER_PATH)
    load = model.split("async function loadModelSettings()", 1)[1].split(
        "function confirmLeave", 1
    )[0]

    assert "createMobileModelLoadRecoveryController" in controller
    assert "createMobileModelLoadRecoveryController" in model
    assert "modelLoadRecovery.beginEntry()" in load
    assert "modelLoadRecovery.settleEntry(readiness)" in load
    assert "modelLoadRecovery.failEntry(error" in load
    assert "onReadinessChange: modelLoadRecovery.onReadinessChange" in model
    assert "onRecoverableIncomplete" in model
    assert "modelLoadRetry.hidden = false" in model


def test_mobile_field_errors_use_own_property_reads_for_stable_ids_and_fields() -> None:
    model = _read(MODEL_PATH)
    controller = _read(CONTROLLER_PATH)
    field_error = model.split("function fieldError(recordId, field)", 1)[1].split(
        "function errorMarkup", 1
    )[0]

    assert "export function readOwnMobileModelFieldError" in controller
    assert "Object.hasOwn(byConnection, recordId)" in controller
    assert "Object.hasOwn(fields, field)" in controller
    assert model.count("readOwnMobileModelFieldError(") >= 2
    assert ".byConnection?.[recordId]?.[field]" not in field_error


def test_mobile_model_feedback_stays_accessible_while_lock_and_busy_are_independent() -> None:
    model = _read(MODEL_PATH)
    boundary_index = model.index('id="mobileModelEditorBoundary"')
    status_index = model.index('id="mobileModelSaveStatus"')
    retry_index = model.index('id="mobileModelLoadRetry"')
    locked = model.split("function setModelEditorLocked(locked)", 1)[1].split(
        "function setModelEditorBusy", 1
    )[0]
    busy = model.split("function setModelEditorBusy(busy)", 1)[1].split(
        "function setModelStatus", 1
    )[0]
    recovery = model.split("createMobileModelLoadRecoveryController({", 1)[1].split(
        "const modelResources", 1
    )[0]

    assert status_index < boundary_index
    assert retry_index < boundary_index
    assert 'aria-live="polite"' in model[status_index : status_index + 120]
    assert 'id="mobileModelSaveButton"' in model[boundary_index:]
    assert "aria-busy" not in locked
    assert 'boundary.setAttribute("aria-busy", busy ? "true" : "false")' in busy
    assert "setBusy: (busy)" in recovery
    assert "if (!disposed) setModelEditorBusy(busy)" in recovery
    assert "setModelEditorLocked(true);\n  setModelEditorBusy(false);" in model

    save = model.split("async function saveModels()", 1)[1].split(
        "async function fetchModelSnapshot", 1
    )[0]
    assert "setModelEditorBusy(true)" in save
    assert "setModelEditorBusy(false)" in save


def test_late_get_remote_reload_and_dirty_navigation_never_overwrite_the_draft() -> None:
    model = _read(MODEL_PATH)

    for marker in (
        "onSnapshotBlocked",
        "receiveRemoteSnapshot",
        "config_reloaded",
        "state.remoteUpdate",
        "function confirmLeave()",
        "requestClose",
        "switchSettingsSection",
        "window.confirm",
        "beforeunload",
    ):
        assert marker in model


def test_disposed_settings_instance_cannot_clobber_a_reopened_overlay() -> None:
    model = _read(MODEL_PATH)

    assert "const byId = (id) => card.querySelector" in model

    probe = model.split("async function probeSelected()", 1)[1].split(
        "function retainSelection", 1
    )[0]
    save = model.split("async function saveModels()", 1)[1].split(
        "async function fetchModelSnapshot", 1
    )[0]
    load = model.split("async function loadModelSettings()", 1)[1].split(
        "function confirmLeave", 1
    )[0]

    assert "if (disposed || !modelOperations.isProbeCurrent(generation)) return" in probe
    assert "if (disposed) return" in save
    assert "if (!disposed)" in save.split("finally", 1)[1]
    assert "if (disposed) return readiness" in load
    assert "if (disposed) return" in load.split("catch", 1)[1]


def test_live_handlers_delegate_to_the_tested_mobile_controller_seams() -> None:
    model = _read(MODEL_PATH)

    update_field = model.split("function updateField(", 1)[1].split("function updateCredential", 1)[
        0
    ]
    update_credential = model.split("function updateCredential(", 1)[1].split(
        "function probeRequestVisible", 1
    )[0]
    show_list = model.split("function showRouteList(", 1)[1].split("function openRouteDetail", 1)[0]
    fetch_snapshot = model.split("async function fetchModelSnapshot", 1)[1].split(
        "async function loadModelSettings", 1
    )[0]
    switch_section = model.split("function switchSettingsSection", 1)[1].split(
        "function onBeforeUnload", 1
    )[0]
    save = model.split("async function saveModels()", 1)[1].split(
        "async function fetchModelSnapshot", 1
    )[0]
    migration_handler = model.split('byId("mobileModelMigrationPanel").addEventListener', 1)[
        1
    ].split("focusController =", 1)[0]

    assert "exactDraftRenderer.afterDraftMutation" in update_field
    assert "exactDraftRenderer.afterDraftMutation" in update_credential
    assert "exactDraftRenderer.beforeRouteList()" in show_list
    assert model.count("exactDraftRenderer.afterDraftMutation()") >= 3
    assert "modelResources.reloadSnapshot()" in fetch_snapshot
    assert "loadModelSettings()" in switch_section
    assert 'id="mobileModelLoadRetry"' in model
    assert 'modelLoadRetry.addEventListener("click"' in model
    assert "!readiness.ready && !readiness.loading" in switch_section
    assert (
        "modelResources.enterModels()"
        in model.split("async function loadModelSettings", 1)[1].split("function confirmLeave", 1)[
            0
        ]
    )
    assert save.index("numericValidation.runSaveIfValid") < save.index("updateModelConfig")
    assert "exactDraftRenderer.afterDraftMutation()" in migration_handler
    assert "resolveOpener" in model


def test_mobile_numeric_preflight_and_error_lifecycle_use_production_seams() -> None:
    model = _read(MODEL_PATH)
    controller = _read(CONTROLLER_PATH)
    save = model.split("async function saveModels()", 1)[1].split(
        "async function fetchModelSnapshot", 1
    )[0]

    for export in (
        "createMobileModelNumericValidationController",
        "normalizeMobileModelValidationDetails",
        "parseMobileModelNumericDraft",
        "validateMobileModelNumbers",
    ):
        assert f"export function {export}" in controller
        assert export in model
    assert save.index("numericValidation.runSaveIfValid") < save.index(
        "updateModelConfig(toModelConfigPayload(state))"
    )
    assert "numericValidation.afterDraftMutation()" in model
    assert model.count("numericValidation.afterAuthoritativeHydration()") >= 3
    assert "normalizeMobileModelValidationDetails(error.details.detail, state)" in save
    assert 'id="mobileModelEmbeddingDimensionError"' in model
    assert 'id="mobileModelEmbeddingSimilarityError"' in model
    assert 'aria-describedby="mobileModelEmbeddingDimensionError"' in model
    assert 'aria-describedby="mobileModelEmbeddingSimilarityError"' in model
    assert "data-mobile-model-num-ctx-error" in model
    assert "mobileModelSelectedNumCtxError" in model
    assert 'field.name === "num_ctx"' in model
    assert 'min="0" step="1" inputmode="numeric"' in model
    assert "parseMobileModelNumericDraft(event.target.value)" in model
    assert "runtimeFieldErrors" not in model


def test_every_error_clearing_mobile_mutation_repaints_numeric_error_hosts() -> None:
    model = _read(MODEL_PATH)
    segments = (
        model.split("function moveSelected(delta)", 1)[1].split("function selectRecord", 1)[0],
        model.split("function updateCredential(", 1)[1].split("function probeRequestVisible", 1)[0],
        model.split('byId("mobileModelEmbeddingEnabled").addEventListener', 1)[1].split(
            "for (const [id, field, kind, path]", 1
        )[0],
        model.split('byId("mobileModelEmbeddingMultimodal").addEventListener', 1)[1].split(
            'byId("mobileModelChatConcurrency")', 1
        )[0],
        model.split('byId("mobileModelMigrationPanel").addEventListener', 1)[1].split(
            "focusController =", 1
        )[0],
    )

    for segment in segments:
        assert "numericValidation.afterDraftMutation()" in segment


def test_opener_list_and_detail_focus_are_restored_with_semantic_controls() -> None:
    model = _read(MODEL_PATH)
    focus_runtime = _read(FOCUS_RUNTIME_PATH)

    for marker in (
        "createDialogFocusController",
        "focusSelectedRouteControl",
        "focusDetailControl",
        "mobileModelInspectorBack",
        "data-model-select",
        "opener",
        ".focus()",
        'type="button"',
        'aria-label="模型路由列表"',
        'aria-label="模型连接详情"',
    ):
        assert marker in model
    assert 'closest?.("[hidden], [inert]")' in focus_runtime


def test_dynamic_model_data_is_escaped_before_inner_html_rendering() -> None:
    model = _read(MODEL_PATH)

    assert "function escapeHtml(" in model
    for value in (
        "record.id",
        "record.name",
        "descriptor.label",
        "descriptor.help",
        "override.path",
        "override.source",
        "error.message",
    ):
        assert f"escapeHtml({value})" in model


def test_mobile_model_controls_have_touch_targets_and_visible_focus() -> None:
    css = _read(CSS_PATH)

    assert ".mobile-model-settings button" in css
    assert "min-height: 44px" in css
    assert ".mobile-model-settings :focus-visible" in css
    assert ".mobile-model-route-layout.is-detail" in css


def test_mobile_does_not_offer_the_one_click_ollama_convenience_path() -> None:
    model = _read(MODEL_PATH)
    config_docs = _read(ROOT / "docs/modules/config.md")
    runtime_docs = _read(ROOT / "docs/modules/runtime.md")

    assert "enableLocalOllamaEmbedding" not in model
    assert "prepareLocalOllamaEmbedding" not in model
    assert "一键启用" not in model
    assert "桌面与插件的一键" not in config_docs
    assert "只有浏览器插件提供" in config_docs
    assert "只有浏览器插件提供" in runtime_docs
    assert "桌面 Web 的 Embedding repair" in runtime_docs
