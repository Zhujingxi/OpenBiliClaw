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

    assert 'id="modelTypeSearch"' in INDEX
    assert 'id="modelConnectionTypeGroups"' in INDEX
    assert 'role="listbox"' in INDEX
    assert "/api/model-connection-types" in model_js
    assert "descriptor.fields" in model_js
    assert "preset_definitions" in model_js
    assert "group.category" in model_js
    assert ".model-connection-type-groups" in CSS
    assert "grid-template-columns: 1fr" in CSS

    assert "data-connection-type-tab" not in INDEX
    assert 'id="llmProvider"' not in INDEX
    assert '<option value="openai">' not in INDEX


def test_route_rows_support_drag_buttons_keyboard_and_focus_restoration() -> None:
    model_js = MODEL_JS_PATH.read_text(encoding="utf-8")

    assert "draggable" in model_js
    assert "model-route-drag-handle" in model_js
    assert "Move Up" in INDEX
    assert "Move Down" in INDEX
    assert "ArrowUp" in model_js
    assert "ArrowDown" in model_js
    assert ".focus()" in model_js


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
    assert ".model-route-layout.is-detail" in CSS
    assert ".model-route-list-pane" in CSS
    assert ".model-inspector-pane" in CSS
    assert 'id="modelInspectorBack"' in INDEX
    assert "data-model-view" in INDEX


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


def test_model_modules_are_loaded_and_cache_busted_by_the_backend() -> None:
    assert 'type="module" src="/web/assets/js/model-settings.js"' in INDEX
    assert 'from "/web/shared/model-config-state.js"' in MODEL_JS_PATH.read_text(encoding="utf-8")
    assert '"assets/js/model-settings.js"' in API_APP
    assert '"shared/model-config-state.js"' in API_APP
    assert re.search(r'app\.mount\(\s*"/web/shared"', API_APP)
