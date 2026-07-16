import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


def test_setup_wizard_static_contract_uses_guided_init_endpoint() -> None:
    """Static guard: setup must reference guided init and not the legacy poke."""
    html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert 'data-panel="3"' in html
    assert "GET /api/init-status" in html or 'fetch("/api/init-status"' in html
    assert 'fetch("/api/init"' in html
    assert "init_progress" in html
    assert "/api/init-completed" not in html


def test_desktop_web_static_contract_exposes_guided_init_cta() -> None:
    """Static guard for the desktop guided-init CTA wiring."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    assert 'initStatus: "/init-status"' in app_js
    assert 'startInit: "/init"' in app_js
    assert "renderInitOnboarding" in app_js
    assert "buildInitChecklist" in app_js
    assert "INIT_SOURCE_OPTIONS" in app_js
    assert "init_progress" in app_js
    # Keep the Docker recovery command in one copyable constant instead of
    # duplicating it in generic reason text.
    assert app_js.count("openbiliclaw init") == 1
    assert (
        'const INIT_CLI_COMMAND = "docker exec -it openbiliclaw-backend openbiliclaw init"'
        in app_js
    )
    unsupported_line = next(line for line in app_js.splitlines() if "unsupported_runtime:" in line)
    assert "复制下方命令" in unsupported_line
    assert ".init-onboarding" in app_css
    assert ".init-progress-fill" in app_css


def test_web_guided_init_polling_is_single_flight() -> None:
    """Runtime-stream events and timer fallback must not compound status polls."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "initPollInFlight" in setup_html
    assert "initPollPending" in setup_html
    assert "scheduleInitPoll(" in setup_html
    assert "initRefreshInFlight" in app_js
    assert "initRefreshPending" in app_js
    assert "scheduleInitStatusRefresh(" in app_js


def test_unknown_init_reasons_remain_diagnosable() -> None:
    """Frontend fallback should surface unknown backend reason codes."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "未知初始化状态" in setup_html
    assert "未知初始化状态" in app_js
    assert re.search(r"INIT_REASON_TEXT\[reason\]\s*\|\|\s*`未知初始化状态", setup_html)
    assert re.search(r"INIT_REASON_TEXT\[reason\]\s*\|\|\s*`未知初始化状态", app_js)


def test_typed_timeout_reasons_prefer_backend_detail_in_web_surfaces() -> None:
    """Timeout details contain the cause/action; a short map label must not hide them."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    for source in (setup_html, app_js):
        assert "analyze_failed" in source
        assert "profile_failed" in source
        assert "discovery_timeout" in source
        assert "detailFirst" in source
        assert "initStatusReasonText(status)" in source


def test_web_surfaces_no_longer_block_reddit_only_init() -> None:
    """Reddit bootstrap events are valid init signals."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "no_profile_signal_sources" not in setup_html
    assert "Reddit 当前只启用 discovery" not in setup_html
    assert "连接你的 B站 账号" not in setup_html
    assert "连接浏览器扩展和平台账号" in setup_html
    assert "reddit.com" in setup_html
    assert "先检查 B站 登录 / AI 服务 / 向量模型" not in setup_html
    assert "所选平台的登录状态" in setup_html
    assert "no_profile_signal_sources" not in app_js
    assert "Reddit 当前只启用 discovery" not in app_js


def test_setup_model_route_is_descriptor_driven_and_provider_first() -> None:
    """Fresh setup consumes the native model APIs and a vertical type list."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert 'id="connectionTypeList"' in setup_html
    assert 'class="connection-type-list"' in setup_html
    assert 'fetch("/api/model-connection-types?capability=chat"' in setup_html
    assert 'fetch("/api/model-config"' in setup_html
    assert 'fetch("/api/model-config/probe"' in setup_html
    assert 'fetch("/api/model-config", {' in setup_html
    assert 'method: "PUT"' in setup_html
    assert "descriptor.fields" in setup_html
    assert "preset_definitions" in setup_html
    model_save = setup_html.split("async function saveModelRoute()", 1)[1].split(
        "async function checkBili()", 1
    )[0]
    assert 'fetch("/api/config"' not in model_save
    assert 'fetch("/api/config", {' not in setup_html
    assert "suppress_background_llm_work" not in setup_html
    assert "llm: {" not in setup_html
    narrow_css = setup_html.split("@media (max-width: 680px)", 1)[1].split("@keyframes", 1)[0]
    assert ".connection-type-list { max-height: none; grid-template-columns: 1fr; }" in narrow_css


def test_setup_narrow_model_editor_has_sequential_detail_and_accessible_back_state() -> None:
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the narrow setup state regression")

    assert 'id="modelSetupLayout"' in setup_html
    assert 'data-narrow-view="list"' in setup_html
    assert 'class="model-connection-list-pane"' in setup_html
    assert 'id="modelTypeBack"' in setup_html
    assert 'aria-label="返回连接类型列表"' in setup_html
    narrow_css = setup_html.split("@media (max-width: 680px)", 1)[1].split("@keyframes", 1)[0]
    assert '.model-setup-layout[data-narrow-view="list"] .model-connection-editor' in narrow_css
    assert (
        '.model-setup-layout[data-narrow-view="detail"] .model-connection-list-pane' in narrow_css
    )
    assert '.model-setup-layout[data-narrow-view="list"] ~ .row-actions' in narrow_css
    assert '$("#modelTypeBack").addEventListener("click", showModelTypeList);' in setup_html
    assert 'setModelNarrowView("detail")' in setup_html

    state_helpers = setup_html.split("    function setModelNarrowView(view) {", 1)[1].split(
        "\n    function descriptorFor", 1
    )[0]
    script = "\n".join(
        [
            'let modelNarrowView = "detail";',
            "const layout = {dataset: {narrowView: 'detail'}};",
            "let focused = false;",
            "const $ = (selector) => selector === '#modelSetupLayout' ? layout : null;",
            ("const document = {querySelector: () => ({focus: () => { focused = true; }})};"),
            "function setModelNarrowView(view) {" + state_helpers,
            "showModelTypeList();",
            (
                "process.stdout.write(JSON.stringify({view: modelNarrowView, "
                "dataset: layout.dataset.narrowView, focused}));"
            ),
        ]
    )
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "view": "list",
        "dataset": "list",
        "focused": True,
    }


def test_setup_preset_and_type_changes_preserve_touched_fields_and_stable_name() -> None:
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the setup draft-state regression")

    model_functions = setup_html.split("    function descriptorFor(type) {", 1)[1].split(
        "\n    function credentialActionPayload", 1
    )[0]
    descriptors = [
        {
            "id": "openai_compatible",
            "label": "OpenAI-compatible",
            "category": "api_protocol",
            "help": "OpenAI-compatible API",
            "fields": [
                {
                    "name": "preset",
                    "capabilities": ["chat"],
                    "presets": [],
                    "input_type": "select",
                },
                {"name": "model", "capabilities": ["chat"], "presets": []},
                {"name": "base_url", "capabilities": ["chat"], "presets": []},
                {"name": "credential", "capabilities": ["chat"], "presets": []},
                {
                    "name": "reasoning_effort",
                    "capabilities": ["chat"],
                    "presets": ["deepseek"],
                },
            ],
            "preset_definitions": [
                {
                    "id": "deepseek",
                    "capabilities": ["chat"],
                    "defaults": {
                        "model": "deepseek-default",
                        "base_url": "https://api.deepseek.com",
                    },
                },
                {
                    "id": "openai",
                    "capabilities": ["chat"],
                    "defaults": {
                        "model": "openai-default",
                        "base_url": "https://api.openai.com/v1",
                    },
                },
            ],
        },
        {
            "id": "ollama",
            "label": "Ollama",
            "category": "local_runtime",
            "help": "Local Ollama",
            "fields": [
                {"name": "model", "capabilities": ["chat"], "presets": []},
                {"name": "base_url", "capabilities": ["chat"], "presets": []},
                {"name": "num_ctx", "capabilities": ["chat"], "presets": []},
            ],
            "preset_definitions": [],
        },
    ]
    draft = {
        "id": "stable-chat",
        "name": "Keep this route name",
        "type": "openai_compatible",
        "model": "deepseek-default",
        "preset": "deepseek",
        "base_url": "https://api.deepseek.com",
        "credential": {
            "action": "keep",
            "value": "",
            "status": {"configured": True, "source": "env"},
        },
        "api_mode": "responses",
        "reasoning_effort": "medium",
        "http_referer": "",
        "x_title": "",
        "num_ctx": 0,
    }
    script = "\n".join(
        [
            f"let modelDescriptors = {json.dumps(descriptors)};",
            f"let draftConnection = {json.dumps(draft)};",
            "let modelSaveInFlight = false;",
            "let modelTouchedFields = new Set();",
            'let modelNarrowView = "list";',
            (
                'const MODEL_VALUE_FIELDS = ["model", "base_url", "api_mode", '
                '"reasoning_effort", "http_referer", "x_title", "num_ctx"];'
            ),
            "function descriptorFor(type) {" + model_functions,
            "renderModelSetup = () => {};",
            "setModelNarrowView = () => {};",
            'updateModelField("model", "user-edited-model");',
            'updateModelField("base_url", "https://user-edited.example/v1");',
            'updateModelField("reasoning_effort", "high");',
            'updateModelField("preset", "openai");',
            "const afterPreset = JSON.parse(JSON.stringify(draftConnection));",
            'selectConnectionType("ollama");',
            "const afterType = JSON.parse(JSON.stringify(draftConnection));",
            "process.stdout.write(JSON.stringify({afterPreset, afterType}));",
        ]
    )
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    state = json.loads(result.stdout)

    for key in ("afterPreset", "afterType"):
        assert state[key]["name"] == "Keep this route name"
        assert state[key]["model"] == "user-edited-model"
        assert state[key]["base_url"] == "https://user-edited.example/v1"
    assert state["afterPreset"]["preset"] == "openai"
    assert state["afterPreset"]["reasoning_effort"] == ""
    assert state["afterType"]["type"] == "ollama"
    assert state["afterType"]["reasoning_effort"] == ""
    assert state["afterType"]["api_mode"] == ""


def test_setup_model_editor_escapes_config_values_and_can_create_first_chat_route() -> None:
    """Config-derived markup is escaped and an empty native route gets one stable draft."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert 'let stableId = "chat-primary"' in setup_html
    assert "connections.length" in setup_html
    assert "createFirstChatDraft()" in setup_html
    assert 'value="${escapeHtml(value)}"' in setup_html
    assert "escapeHtml(status.credential_ref" in setup_html
    assert "escapeHtml(draftConnection.credential.value" in setup_html

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the inline escaping regression")
    helper = setup_html.split("    function escapeHtml(value) {", 1)[1].split(
        "\n    }\n\n    function descriptorFor", 1
    )[0]
    function_source = "function escapeHtml(value) {" + helper + "\n}"
    attack = '"><img src=x onerror="globalThis.injected=true">'
    result = subprocess.run(
        [
            node,
            "-e",
            f"{function_source}; process.stdout.write(escapeHtml({json.dumps(attack)}));",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert (
        result.stdout == "&quot;&gt;&lt;img src=x onerror=&quot;globalThis.injected=true&quot;&gt;"
    )
    assert "<img" not in result.stdout


def test_setup_empty_chat_route_allocates_globally_unique_stable_id() -> None:
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the stable-ID regression")

    safe_status = setup_html.split("    function safeCredentialStatus(credential) {", 1)[1].split(
        "\n    }\n\n    function hydrateDraftConnection", 1
    )[0]
    create_draft = setup_html.split("    function createFirstChatDraft() {", 1)[1].split(
        "\n    }\n\n    function currentPreset", 1
    )[0]
    apply_defaults = setup_html.split(
        "    function applyPresetDefaults(descriptor, preset, force = false) {", 1
    )[1].split("\n    }\n\n    function selectConnectionType", 1)[0]
    script = "\n".join(
        [
            "let draftConnection = null;",
            (
                "let modelSnapshot = {models: {chat: {connections: []}, "
                "embedding: {providers: [{id: 'chat-primary'}]}}};"
            ),
            (
                "let modelDescriptors = [{id: 'ollama', label: 'Ollama', "
                "category: 'local_runtime', fields: [], preset_definitions: []}];"
            ),
            "function safeCredentialStatus(credential) {" + safe_status + "\n}",
            "function applyPresetDefaults(descriptor, preset, force = false) {"
            + apply_defaults
            + "\n}",
            "function createFirstChatDraft() {" + create_draft + "\n}",
            "process.stdout.write(createFirstChatDraft().id);",
        ]
    )

    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == "chat-primary-2"


def test_setup_model_save_requires_exact_probe_before_revisioned_put() -> None:
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    save = setup_html.split("async function saveModelRoute()", 1)[1].split(
        "async function checkBili()", 1
    )[0]

    assert save.index('fetch("/api/model-config/probe"') < save.index(
        'fetch("/api/model-config", {'
    )
    assert "revision: modelSnapshot.revision" in save
    assert "connection: primaryDraft" in save


def test_setup_model_save_preserves_untouched_native_route_payload_fields() -> None:
    """Only the selected primary is editable; every other API field stays equivalent."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the model payload regression")

    function_names = (
        "credentialActionPayload",
        "chatConnectionPayload",
        "embeddingProviderPayload",
        "buildModelConfigPayload",
    )
    function_sources: list[str] = []
    for index, function_name in enumerate(function_names):
        next_name = (
            function_names[index + 1] if index + 1 < len(function_names) else "validateModelDraft"
        )
        body = setup_html.split(f"    function {function_name}", 1)[1].split(
            f"\n    function {next_name}", 1
        )[0]
        function_sources.append(f"function {function_name}{body}\n")

    snapshot = {
        "revision": "revision-14",
        "models": {
            "schema_version": 1,
            "chat": {
                "concurrency": 7,
                "timeout_seconds": 123.5,
                "connections": [
                    {
                        "id": "primary",
                        "name": "Primary",
                        "type": "openai_compatible",
                        "model": "primary-model",
                        "preset": "deepseek",
                        "base_url": "https://primary.example/v1",
                        "credential": {"configured": True, "source": "inline"},
                        "api_mode": "responses",
                        "reasoning_effort": "high",
                        "http_referer": "https://primary.example",
                        "x_title": "Primary title",
                        "num_ctx": 4096,
                        "probe": {"ok": True},
                        "circuit": {"state": "closed"},
                    },
                    {
                        "id": "fallback",
                        "name": "Fallback",
                        "type": "anthropic_compatible",
                        "model": "fallback-model",
                        "preset": "anthropic",
                        "base_url": "https://fallback.example/v1",
                        "credential": {"configured": True, "source": "env"},
                        "api_mode": "messages",
                        "reasoning_effort": "medium",
                        "http_referer": "https://fallback.example",
                        "x_title": "Fallback title",
                        "num_ctx": 8192,
                        "probe": {"ok": False},
                        "circuit": {"state": "open"},
                    },
                ],
            },
            "embedding": {
                "enabled": True,
                "settings": {
                    "model": "shared-embedding-model",
                    "output_dimensionality": 1536,
                    "similarity_threshold": 0.73,
                    "multimodal_enabled": True,
                },
                "providers": [
                    {
                        "id": "embedding-remote",
                        "name": "Remote embedding",
                        "type": "openai_compatible",
                        "preset": "openai",
                        "base_url": "https://embedding.example/v1",
                        "credential": {"configured": True, "source": "env"},
                        "probe": {"ok": True},
                        "circuit": {"state": "closed"},
                    }
                ],
            },
        },
    }
    script = "\n".join(
        [
            f"const modelSnapshot = {json.dumps(snapshot)};",
            *function_sources,
            "const primary = chatConnectionPayload(modelSnapshot.models.chat.connections[0]);",
            "process.stdout.write(JSON.stringify(buildModelConfigPayload(primary)));",
        ]
    )
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    expected_fallback = {
        key: snapshot["models"]["chat"]["connections"][1][key]
        for key in (
            "id",
            "name",
            "type",
            "model",
            "preset",
            "base_url",
            "api_mode",
            "reasoning_effort",
            "http_referer",
            "x_title",
            "num_ctx",
        )
    }
    expected_fallback["credential"] = {"action": "keep"}
    expected_embedding = {
        key: snapshot["models"]["embedding"]["providers"][0][key]
        for key in ("id", "name", "type", "preset", "base_url")
    }
    expected_embedding["credential"] = {"action": "keep"}

    assert payload["revision"] == snapshot["revision"]
    assert payload["models"]["chat"]["concurrency"] == 7
    assert payload["models"]["chat"]["timeout_seconds"] == 123.5
    assert payload["models"]["chat"]["connections"][1] == expected_fallback
    assert payload["models"]["embedding"] == {
        "enabled": True,
        "settings": snapshot["models"]["embedding"]["settings"],
        "providers": [expected_embedding],
    }
    assert "probe" not in json.dumps(payload)
    assert "circuit" not in json.dumps(payload)


def test_setup_revision_conflict_rehydrates_the_latest_native_snapshot() -> None:
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    conflict_handler = setup_html.split("function handleModelConflict(details)", 1)[1].split(
        "async function loadModelSetup()", 1
    )[0]
    save = setup_html.split("async function saveModelRoute()", 1)[1].split(
        "async function checkBili()", 1
    )[0]

    assert "details?.latest" in conflict_handler
    assert "hydrateModelSetup(details.latest)" in conflict_handler
    assert 'error.status === 409 && error.details?.error === "revision_conflict"' in save
    assert "handleModelConflict(error.details)" in save


def test_setup_init_sources_are_explicit_opt_in_without_settings_enable_block() -> None:
    """Checked setup sources are this-run opt-ins, not a filter over settings toggles."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "勾选会同时开启该来源" in setup_html
    assert "selectedSourcesNeedingEnable" not in setup_html
    assert "还没在设置里开启" not in setup_html
    for source in ("bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu", "reddit"):
        assert f'key: "{source}"' in setup_html
        assert f'key: "{source}"' in app_js


def test_guided_init_web_docs_belong_to_v03110_release_block() -> None:
    """Do not retroactively claim already-released v0.3.109 shipped web Phase 2."""
    version_py = Path("src/openbiliclaw/__init__.py").read_text(encoding="utf-8")
    changelog = Path("docs/changelog.md").read_text(encoding="utf-8")
    gui_spec = Path("docs/specs/gui-init.md").read_text(encoding="utf-8")

    # Web Phase 2 shipped in v0.3.111 — the project version must never sit
    # below that (an exact pin here would break on every release bump).
    match = re.search(r'__version__ = "(\d+)\.(\d+)\.(\d+)"', version_py)
    assert match is not None
    assert tuple(int(part) for part in match.groups()) >= (0, 3, 111)
    top_block = changelog.split("## v0.3.109", 1)[0]
    assert "/setup/" in top_block
    assert "/web" in top_block
    assert "已落地 v0.3.111" in gui_spec
    assert "已落地 v0.3.109" not in gui_spec


def test_init_onboarding_gate_trusts_init_status_when_runtime_status_is_unavailable() -> None:
    """The guided-init gate must not depend solely on state.runtimeStatus.

    runtime-status can be transiently unreachable (hydrate re-fetch swallowed
    into null) or rebuilt from field-less runtime events whose missing
    `initialized` normalizes to true. /api/init-status stays the authoritative
    pre-init source, so an explicit initialized=false there must still surface
    the guided-init card.
    """
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    gate = app_js.split("function shouldShowInitOnboarding(", 1)[1]
    gate = gate.split("\n    }", 1)[0]
    assert "state.initStatus?.initialized === false" in gate
    assert "hasPostInitRuntimeSignals" not in gate


def test_hydrate_runtime_status_fallback_is_not_dead_catch() -> None:
    """Progressive runtime reads apply and recover independently."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    assert "async function hydrateFromBackend()" in app_js
    hydrate = app_js.split("async function hydrateFromBackend()", 1)[1]
    hydrate = hydrate.split("\n    function renderAll()", 1)[0]

    # The first runtime read has its own immediate application/recovery branch.
    assert "const firstRuntimeGeneration = desktopRuntimeGeneration;" in hydrate
    assert "const runtimePromise = readRuntimeSnapshot();" in hydrate
    assert "const runtimeApplicationPromise = runtimePromise.then(" in hydrate
    assert "(snapshot) => applyInitialRuntimeSnapshot(snapshot)" in hydrate
    assert "() => markDesktopRuntimeFailedAndRecover()" in hydrate
    assert "if (firstRuntimeGeneration !== desktopRuntimeGeneration) return;" in hydrate
    assert "applyDesktopRuntimeSnapshot(snapshot, firstRuntimeGeneration)" in hydrate

    # Recommendation settlement starts a separate freshness reread, guarded
    # against newer runtime-stream generations.
    assert "const runtimeReconciliationPromise = recommendationApplicationPromise.then(" in hydrate
    assert "() => reconcileRuntimeAfterRecommendations()" in hydrate
    assert "const secondRuntimeGeneration = desktopRuntimeGeneration;" in hydrate
    assert "await readRuntimeSnapshot()" in hydrate
    assert "if (runtimeReconciliationGeneration !== desktopRuntimeGeneration) return;" in hydrate

    # Initial rejection enters the existing bounded runtime recovery owner.
    assert 'desktopRuntimeLoadState = "failed";' in hydrate
    assert "scheduleDesktopRuntimeRecovery();" in hydrate
    assert "renderDesktopRuntimeFailure();" in hydrate


def test_bili_checklist_label_reflects_probe_result_and_surfaces_detail() -> None:
    """A failed B站 probe must never render a label containing "已登录".

    Field report (2026-07): with a proxy on, the login probe fails while the
    user IS logged in in the browser. Unchecking B站 demoted the row to the
    soft "B站 已登录（未勾选 B 站，可跳过）" label — which reads as "logged in
    now". Labels must state the actual probe result, and the failure hint must
    carry the backend's `bilibili_detail` (cookie-expired vs proxy-broken).
    """
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "B站 登录检测未通过" in setup_html
    assert "B 站登录检测未通过" in app_js
    for text in (setup_html, app_js):
        # The old unconditional "已登录（未勾选…" label is gone.
        assert "已登录（未勾选" not in text
        assert "bilibili_detail" in text


def test_runtime_stream_open_rehydrates_when_backend_data_never_loaded() -> None:
    """Frozen-entry race: /web can load before the backend binds, and the boot
    hydrate swallows every failure into nulls. An uninitialized backend emits
    no runtime events, so without a re-hydrate on the first successful
    runtime-stream connect the guided-init card would stay hidden forever."""
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    open_handler = app_js.split('socket.addEventListener("open"', 1)[1]
    open_handler = open_handler.split('socket.addEventListener("message"', 1)[0]
    guard = "if (!state.initStatus && !state.runtimeStatus) {"
    authenticate = "void ensureAuthenticated()"
    schedule = ".then(scheduleBackendHydration)"
    safe_rejection = ".catch(() => {})"

    assert guard in open_handler
    assert authenticate in open_handler
    assert schedule in open_handler
    assert safe_rejection in open_handler
    assert open_handler.index(guard) < open_handler.index(authenticate)
    assert open_handler.index(authenticate) < open_handler.index(schedule)
    assert open_handler.index(schedule) < open_handler.index(safe_rejection)


def test_setup_wizard_guard_resumes_running_and_initialized_states_on_load() -> None:
    """A mid-init reload must re-attach to progress instead of landing on step 0,
    and an initialized backend must not re-present the LLM form."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    guard = setup_html.split("(async function guard()", 1)[1]
    assert "fetchInitStatus()" in guard
    assert "if (status.running)" in guard
    assert "renderInitProgress(status)" in guard
    assert "connectInitStream()" in guard
    assert "if (status.initialized)" in guard
    assert "renderWaitingForFirstPool(status)" in guard
    assert "shouldResumeRecovery" in guard
    assert 'initStartMode(status) === "cli_only"' in guard
    assert "renderCliInitRequired(status)" in guard


def test_desktop_recovery_routes_model_failures_and_hides_internal_event_codes() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    settings = app_js.split("function preferredInitSettingsPanel", 1)[1].split(
        "function initEnabledPlatforms", 1
    )[0]
    assert '"analyze_failed"' in settings
    assert '"profile_failed"' in settings
    assert 'return "models"' in settings

    event_summary = app_js.split("function runtimeEventSummary", 1)[1].split(
        "function handleRuntimeEvent", 1
    )[0]
    assert "bilibili_cookie_synced" in event_summary
    assert "B 站登录信息已同步" in event_summary
    assert "return labels[event?.type]" in event_summary
    handler = app_js.split("function handleRuntimeEvent", 1)[1].split(
        "function hydrateFromBackend", 1
    )[0]
    assert "event.message || event.live_summary || event.type" not in handler


def test_desktop_model_route_selection_resets_detail_scroll() -> None:
    model_js = Path("src/openbiliclaw/web/desktop/assets/js/model-settings.js").read_text(
        encoding="utf-8"
    )
    selection = model_js.split("function selectRecord", 1)[1].split("function addConnection", 1)[0]
    assert 'window.scrollTo({ top: 0, behavior: "auto" })' in selection


def test_setup_wizard_hydrates_credential_status_without_secret_value() -> None:
    """A configured key becomes a keep action, never an editable placeholder."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert "credential.status" in setup_html
    assert 'action: "keep"' in setup_html
    assert 'autocomplete="new-password"' in setup_html
    assert "savedKeyProviders" not in setup_html
    assert "masked" not in setup_html.lower()


def test_setup_wizard_first_pool_wait_has_web_escape_hatch() -> None:
    """The 95% waiting state must never park the user on a disabled button with
    no way out of the wizard."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert 'id="initEscape"' in setup_html
    assert '<a href="/web">' in setup_html
    waiting = setup_html.split("function renderWaitingForFirstPool(status = null)", 1)[1].split(
        "\n    }", 1
    )[0]
    assert '$("#initEscape").className = "msg show info";' in waiting


def test_issue72_gateway_fields_present_on_all_config_surfaces() -> None:
    """issue #72 — third-party gateway controls exist on every web config
    surface: Claude gets an optional Base URL, the OpenAI-protocol family
    gets an api_flavor (/v1/responses) selector, and stale Base URLs are
    never submitted for providers that don't show the field."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    desktop_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    model_js = Path("src/openbiliclaw/web/desktop/assets/js/model-settings.js").read_text(
        encoding="utf-8"
    )

    # /setup/ renders these conditionally from the backend descriptor instead
    # of hard-coding OpenAI/Anthropic gateway rules.
    assert 'id="modelDescriptorFields"' in setup_html
    assert "descriptor.fields" in setup_html
    assert "field.capabilities" in setup_html
    assert "field.presets" in setup_html
    assert "field.choices" in setup_html

    # Desktop settings render api_mode for every selected route record from
    # the backend descriptor instead of duplicating primary/fallback controls.
    assert 'id="modelDescriptorFields"' in desktop_html
    assert "/api/model-connection-types" in model_js
    assert "descriptor.fields" in model_js
    assert "field.choices" in model_js
    assert "data-model-field" in model_js
    assert 'id="llmApiFlavor"' not in desktop_html
    assert 'id="llmFallbackApiFlavor"' not in desktop_html


def test_setup_wizard_config_save_401_points_to_login_instead_of_dead_end() -> None:
    """The model API is session-gated while init endpoints are public: an
    auth-enabled remote browser must get a login path on save, not a bare
    "保存失败：HTTP 401" dead end at step 0."""
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert "r.status === 401" in setup_html
    assert "输入访问密码登录" in setup_html
    assert '<a href="/web">' in setup_html


def test_web_surfaces_offer_embedding_repair_and_progress() -> None:
    """Both web init checklists expose one-click model download + live progress.

    The repair button POSTs /api/embedding/repair; while the pull runs the
    backend classifies embedding_check="repairing" and the pages keep polling
    so the row's hint shows live percent (user request 2026-07-05).
    """
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    app_css = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")

    for surface in (setup_html, app_js):
        assert "data-embedding-repair" in surface
        assert "embedding_detail" in surface
        assert "embedding_pull_status" in surface
        assert "ollama_phase" in surface
        assert "Ollama 启动中" in surface
        assert "model_missing" in surface and "model_broken" in surface
        assert "model_path_encoding" in surface
        assert "disk_full" in surface and "network" in surface and "model_oom" in surface
        assert "provider_error" in surface
        assert "迁移模型目录并修复" in surface
        assert "重新检测" in surface
        assert "embeddingPullProgressView" in surface
    assert '"/api/embedding/repair"' in setup_html
    assert "embedding_repair_running" in setup_html  # keeps polling while downloading
    assert 'embeddingRepair: "/embedding/repair"' in app_js
    assert "handleEmbeddingRepairClick" in app_js
    assert ".init-repair-btn" in setup_html
    assert ".init-repair-btn" in app_css


# ── init-progress-visibility Phase 0: API models + heartbeat task ────────────


def _progress_coord(tmp_path):
    from types import SimpleNamespace

    from openbiliclaw.runtime.init_coordinator import InitCoordinator
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "hb.db")
    db.initialize()
    ctx = SimpleNamespace(database=db, event_hub=None, runtime_controller=None)
    return InitCoordinator(ctx), db


def test_init_stage_out_accepts_and_omits_progress_fields() -> None:
    """InitStageOut stays backward-compatible: old stage dicts (no progress /
    eta_seconds) parse, and new ones nest InitStageProgressOut."""
    from openbiliclaw.api.models import InitStageOut, InitStageProgressOut

    legacy = InitStageOut(n=2, label="分析偏好", status="pending", reason=None)
    assert legacy.progress is None
    assert legacy.eta_seconds is None

    rich = InitStageOut(
        n=2,
        label="分析偏好",
        status="running",
        reason=None,
        progress={"done": 3, "total": 8, "note": "第 3/8 批"},
        eta_seconds=180,
    )
    assert isinstance(rich.progress, InitStageProgressOut)
    assert rich.progress.done == 3 and rich.progress.total == 8
    assert rich.eta_seconds == 180


def test_init_status_out_has_last_activity_default() -> None:
    from openbiliclaw.api.models import InitStatusOut

    assert InitStatusOut().last_activity == ""
    assert InitStatusOut().start_mode == "web"
    assert InitStatusOut().last_failure_reason == ""
    assert InitStatusOut().last_failure_detail == ""


def test_heartbeat_interval_bounds_last_activity_freshness() -> None:
    """Goal metric 1 fallback: the heartbeat period must be ≤30s so that a
    65s hung stage still lands ≥2 touches (last_activity stays ≤30s fresh)."""
    from openbiliclaw.api.app import _INIT_HEARTBEAT_INTERVAL_SECONDS

    assert _INIT_HEARTBEAT_INTERVAL_SECONDS <= 30


async def test_heartbeat_task_keeps_touching_until_cancelled(tmp_path) -> None:
    import asyncio
    from contextlib import suppress

    from openbiliclaw.api.app import _run_init_heartbeat

    coord, db = _progress_coord(tmp_path)
    coord.try_start("run-1")
    await coord.mark_running("run-1")
    seq_before = db.get_latest_init_run()["sequence"]

    task = asyncio.create_task(_run_init_heartbeat(coord, "run-1", interval=0.01))
    await asyncio.sleep(0.06)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    seq_after = db.get_latest_init_run()["sequence"]
    # At least two heartbeat touches landed while the task ran.
    assert seq_after - seq_before >= 2
    assert coord.get_status()["last_activity"] != ""


async def test_heartbeat_swallows_touch_errors(tmp_path) -> None:
    import asyncio
    from contextlib import suppress

    from openbiliclaw.api.app import _run_init_heartbeat

    class _BoomCoord:
        async def touch(self, run_id: str) -> None:
            raise RuntimeError("db gone")

    # A failing touch must not kill the heartbeat loop (it just logs WARNING).
    task = asyncio.create_task(_run_init_heartbeat(_BoomCoord(), "run-1", interval=0.01))
    await asyncio.sleep(0.03)
    assert not task.done()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def test_init_status_endpoint_surfaces_last_activity(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "e1.db")
    db.initialize()
    app = create_app(memory_manager=object(), database=db, soul_engine=object())
    with TestClient(app) as client:
        body = client.get("/api/init-status").json()
    assert "last_activity" in body
    assert isinstance(body["last_activity"], str)


# ── init-progress-visibility Phase 1: run_guided_init producer wiring ─────────


class _RecordingCoordinator:
    """Records the progress signals run_guided_init emits (no DB needed)."""

    def __init__(self) -> None:
        self.stage_progress_calls: list[dict] = []
        self.started_stages: list[int] = []
        self.done_stages: list[int] = []

    async def stage_started(self, run_id: str, n: int) -> None:
        self.started_stages.append(n)

    async def stage_done(self, run_id: str, n: int, *, status: str = "ok", reason=None) -> None:
        self.done_stages.append(n)

    async def stage_progress(
        self, run_id: str, stage: int, *, done: int, total: int, note=None
    ) -> None:
        self.stage_progress_calls.append(
            {"stage": stage, "done": done, "total": total, "note": note}
        )

    def register_enqueued_task(self, run_id: str, task_id: str) -> None:
        pass


class _StubEngine:
    def __init__(self, chunk_reports: int = 3) -> None:
        self.chunk_reports = chunk_reports
        self.received_callback = None

    async def analyze_events(self, events, *, event_chunk_size=0, progress_callback=None):
        self.received_callback = progress_callback
        if progress_callback is not None:
            for i in range(1, self.chunk_reports + 1):
                await progress_callback(i, self.chunk_reports)

    async def build_initial_profile(self, history):
        return object()


class _StubMemory:
    async def propagate_event(self, event) -> None:
        pass


def _patch_run_guided_init_collectors(monkeypatch, engine) -> None:
    import openbiliclaw.cli as cli

    async def _fetch_bili(client, *, history_limit, favorite_limit, follow_limit):
        return ([{"title": "hist-1"}], [], [])

    async def _rwp(coro, **kwargs):
        return await coro

    async def _discover_backfill(profile, *, target_pool_count, label_suffix=""):
        return 0

    monkeypatch.setattr(cli, "_fetch_bilibili_init_data", _fetch_bili)
    monkeypatch.setattr(
        cli, "_history_item_to_event", lambda item: {"event_type": "view", "title": "hist-1"}
    )
    monkeypatch.setattr(cli, "_collect_xhs_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_dy_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_yt_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_zhihu_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_collect_reddit_bootstrap_events", lambda tid: ([], {}, "timeout"))
    monkeypatch.setattr(cli, "_enqueue_reddit_bootstrap_task", lambda kick=True: "task-r")
    monkeypatch.setattr(cli, "_kick_task_dispatcher", lambda source: None)
    monkeypatch.setattr(cli, "_build_draft_profile_for_discover", lambda memory: object())
    monkeypatch.setattr(cli, "_run_with_progress", _rwp)
    monkeypatch.setattr(cli, "_print_section_title", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_maybe_update_init_source_shares", lambda *a, **k: None)
    return _discover_backfill


async def test_run_guided_init_emits_stage_progress_for_sources_and_chunks(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    engine = _StubEngine(chunk_reports=3)
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)
    coord = _RecordingCoordinator()

    await cli.run_guided_init(
        client=object(),
        memory=_StubMemory(),
        soul_engine=engine,
        favorite_limit=0,
        follow_limit=0,
        include_bili=True,
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        include_x=False,
        include_zhihu=False,
        include_reddit=True,
        target_pool_count=0,
        discover_backfill=discover_backfill,
        coordinator=coord,
        run_id="run-1",
    )

    stage1 = [c for c in coord.stage_progress_calls if c["stage"] == 1]
    # Two selected sources: B站 then Reddit — note switches, done increments 0→1.
    assert [(c["done"], c["total"], c["note"]) for c in stage1] == [
        (0, 2, "正在采集 B 站"),
        (1, 2, "正在采集 Reddit"),
    ]

    stage2 = [c for c in coord.stage_progress_calls if c["stage"] == 2]
    assert [(c["done"], c["total"]) for c in stage2] == [(1, 3), (2, 3), (3, 3)]
    assert stage2[-1]["note"] == "第 3/3 批"

    # Stages all completed (Task 1 clears any progress residue on stage_done).
    assert coord.done_stages == [1, 2, 3, 4]


async def test_run_guided_init_cli_path_uses_console_progress_callback(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    engine = _StubEngine(chunk_reports=2)
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)

    # No coordinator (CLI path): analyze_events must still receive a callback,
    # and invoking it must not raise (it prints instead of hitting a coordinator).
    await cli.run_guided_init(
        client=object(),
        memory=_StubMemory(),
        soul_engine=engine,
        favorite_limit=0,
        follow_limit=0,
        include_bili=True,
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        include_x=False,
        include_zhihu=False,
        include_reddit=False,
        target_pool_count=0,
        discover_backfill=discover_backfill,
        coordinator=None,
        run_id=None,
    )
    assert engine.received_callback is not None
    await engine.received_callback(1, 2)  # prints without error


async def test_run_guided_init_bounds_hung_preference_analysis(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    class _HangingAnalyzeEngine(_StubEngine):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled = False

        async def analyze_events(self, events, *, event_chunk_size=0, progress_callback=None):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    engine = _HangingAnalyzeEngine()
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)
    coord = _RecordingCoordinator()

    with pytest.raises(cli.GuidedInitError) as excinfo:
        await cli.run_guided_init(
            client=object(),
            memory=_StubMemory(),
            soul_engine=engine,
            favorite_limit=0,
            follow_limit=0,
            include_bili=True,
            include_xhs=False,
            include_dy=False,
            include_yt=False,
            target_pool_count=0,
            discover_backfill=discover_backfill,
            coordinator=coord,
            run_id="run-timeout",
            profile_analysis_timeout_seconds=0.01,
        )

    assert excinfo.value.reason == "analyze_failed"
    assert "超过 6 分钟" in excinfo.value.message
    assert "Base URL" in excinfo.value.message
    assert "模型名" in excinfo.value.message
    assert "重试初始化" in excinfo.value.message
    assert engine.cancelled is True
    assert coord.started_stages == [1, 2]
    assert coord.done_stages == [1]


async def test_run_guided_init_bounds_hung_profile_build(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    class _HangingProfileEngine(_StubEngine):
        def __init__(self) -> None:
            super().__init__(chunk_reports=0)
            self.cancelled = False

        async def build_initial_profile(self, history):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    engine = _HangingProfileEngine()
    discover_backfill = _patch_run_guided_init_collectors(monkeypatch, engine)

    with pytest.raises(cli.GuidedInitError) as excinfo:
        await cli.run_guided_init(
            client=object(),
            memory=_StubMemory(),
            soul_engine=engine,
            favorite_limit=0,
            follow_limit=0,
            include_bili=True,
            include_xhs=False,
            include_dy=False,
            include_yt=False,
            target_pool_count=0,
            discover_backfill=discover_backfill,
            profile_build_timeout_seconds=0.01,
        )

    assert excinfo.value.reason == "profile_failed"
    assert "超过 6 分钟" in excinfo.value.message
    assert "Base URL" in excinfo.value.message
    assert "模型名" in excinfo.value.message
    assert "重试初始化" in excinfo.value.message
    assert engine.cancelled is True


async def test_run_guided_init_treats_hung_discovery_as_partial_success(monkeypatch) -> None:
    import openbiliclaw.cli as cli

    engine = _StubEngine(chunk_reports=0)
    _patch_run_guided_init_collectors(monkeypatch, engine)
    discovery_cancelled = False

    async def _hanging_discovery(profile, *, target_pool_count, label_suffix=""):
        nonlocal discovery_cancelled
        try:
            await asyncio.Event().wait()
        finally:
            discovery_cancelled = True

    result = await cli.run_guided_init(
        client=object(),
        memory=_StubMemory(),
        soul_engine=engine,
        favorite_limit=0,
        follow_limit=0,
        include_bili=True,
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        target_pool_count=0,
        discover_backfill=_hanging_discovery,
        discovery_timeout_seconds=0.01,
    )

    assert result.discovery_error is True
    assert isinstance(result.discover_exc, TimeoutError)
    assert result.discovery_reason == "discovery_timeout"
    assert "超过 10 分钟" in result.discovery_detail
    assert "部分完成" in result.discovery_detail
    assert "后台继续补池" in result.discovery_detail
    assert discovery_cancelled is True
