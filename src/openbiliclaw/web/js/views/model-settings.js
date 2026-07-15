import {
  fetchConfig,
  fetchModelConfig,
  fetchModelConnectionTypes,
  probeModelConnection,
  updateConfig,
  updateModelConfig,
} from "../api.js";
import { createDialogFocusController } from "../saved-sync-runtime.js";
import {
  MAX_ROUTE_ITEMS,
  appendRouteItem,
  applyLatestSnapshotRequest,
  applyPreset,
  applyProbeResult,
  changeConnectionType,
  changePreset,
  createLatestRequestGate,
  createModelOperationGate,
  createProbeSignature,
  hydrateModelConfig,
  loadIndependentModelResources,
  mapServerFieldErrors,
  moveRouteItem,
  probeSignatureMatches,
  receiveRemoteSnapshot,
  removeRouteItem,
  selectRouteItem,
  selectedRecord,
  setMigrationResolution,
  toModelConfigPayload,
  updateRouteField,
  updateRouteSetting,
} from "../../shared/model-config-state.js";

const CONFIG_RELOADED_TYPE = "config_reloaded";
const CATEGORY_LABELS = {
  api_protocol: "API protocols",
  local_runtime: "Local runtimes",
  oauth: "OAuth connections",
};
const ROUTE_OVERRIDE_PATHS = {
  chat: "models.chat.connections",
  embedding: "models.embedding.providers",
};

let requestCloseActiveSettings = null;

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>'"]/g,
    (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "'": "&#39;",
      '"': "&quot;",
    })[character],
  );
}

function disabledMarkup(disabled) {
  return disabled ? ' disabled aria-disabled="true"' : "";
}

function buildSavedSyncUpdate(enabled) {
  return { saved_sync: { auto_sync_enabled: Boolean(enabled) } };
}

/**
 * Open the mobile settings dialog. Saved Sync and Models retain separate
 * persistence owners; model state never passes through the legacy config API.
 */
export async function openMobileSettings(opener) {
  if (requestCloseActiveSettings && !requestCloseActiveSettings()) return null;
  document.getElementById("mobile-settings-overlay")?.remove();

  const overlay = document.createElement("section");
  overlay.id = "mobile-settings-overlay";
  overlay.className = "mobile-settings-overlay";
  overlay.setAttribute("aria-label", "OpenBiliClaw 设置");
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.tabIndex = -1;

  const card = document.createElement("div");
  card.className = "mobile-settings-card mobile-model-settings";
  card.innerHTML = `
    <div class="mobile-settings-head">
      <div><p class="eyebrow">Settings</p><h2>设置</h2></div>
      <button class="mobile-settings-close" type="button" aria-label="关闭设置">×</button>
    </div>
    <nav class="mobile-settings-sections" role="tablist" aria-label="设置分类">
      <button class="mobile-settings-section is-active" type="button" role="tab"
        aria-selected="true" data-mobile-settings-section="saved">保存与同步</button>
      <button class="mobile-settings-section" type="button" role="tab"
        aria-selected="false" data-mobile-settings-section="models">Models</button>
    </nav>

    <section class="mobile-settings-panel" data-mobile-settings-panel="saved">
      <h3 class="mobile-settings-panel-title" tabindex="-1">保存与同步</h3>
      <label class="mobile-settings-field" for="mobile-saved-auto-sync">
        <input id="mobile-saved-auto-sync" type="checkbox">
        <span>保存时自动同步到对应平台</span>
      </label>
      <p class="mobile-settings-hint">默认关闭。收藏和稍后再看始终先保存在本地；关闭时仍可在列表页手动同步。</p>
      <p class="mobile-settings-status" aria-live="polite"></p>
      <button class="mobile-settings-retry btn btn-outline" type="button" hidden>重试加载</button>
      <div class="mobile-settings-actions">
        <button class="mobile-settings-save btn btn-brand" type="button">保存设置</button>
      </div>
    </section>

    <section class="mobile-settings-panel" data-mobile-settings-panel="models" hidden>
      <h3 class="mobile-settings-panel-title" tabindex="-1">Models</h3>
      <fieldset id="mobileModelEditorBoundary" class="mobile-model-editor-boundary"
        aria-label="模型路由编辑器" aria-busy="false">
        <div class="mobile-model-route-tabs" role="tablist" aria-label="模型类型">
          <button class="mobile-model-route-tab is-active" type="button" role="tab"
            aria-selected="true" data-mobile-model-route="chat">Chat route</button>
          <button class="mobile-model-route-tab" type="button" role="tab"
            aria-selected="false" data-mobile-model-route="embedding">Embedding route</button>
          <button class="mobile-model-route-tab" type="button" role="tab"
            aria-selected="false" data-mobile-model-route="runtime">Runtime</button>
        </div>

        <div id="mobileModelRemoteBanner" class="mobile-model-banner" role="status" hidden>
          <span>模型配置已在别处更新。当前草稿仍保留。</span>
          <button id="mobileModelReloadRemote" type="button">放弃草稿并重新加载</button>
        </div>
        <section id="mobileModelOverrideNotice" class="mobile-model-overrides"
          aria-label="只读模型配置覆盖" role="status" hidden></section>
        <div id="mobileModelErrorSummary" class="mobile-model-error-summary"
          role="alert" hidden></div>
        <section id="mobileModelMigrationPanel" class="mobile-model-migration"
          aria-label="旧模型配置迁移" hidden></section>

        <section data-mobile-model-view="route">
          <section id="mobileModelEmbeddingSharedSettings"
            class="mobile-model-embedding-shared" aria-label="Embedding 共享设置" hidden>
            <div class="mobile-model-section-heading">
              <div><p class="eyebrow">Shared vector space</p><h3>Embedding 共享设置</h3></div>
              <label class="mobile-model-toggle">
                <input id="mobileModelEmbeddingEnabled" type="checkbox">
                <span>启用</span>
              </label>
            </div>
            <label class="mobile-model-field">
              <span>共享模型</span>
              <input id="mobileModelEmbeddingModel" autocomplete="off">
            </label>
            <label class="mobile-model-field">
              <span>输出维度</span>
              <input id="mobileModelEmbeddingDimension" type="number" min="0" step="1"
                inputmode="numeric">
            </label>
            <label class="mobile-model-field">
              <span>相似度阈值</span>
              <input id="mobileModelEmbeddingSimilarity" type="number" min="0" max="1"
                step="0.01" inputmode="decimal">
            </label>
            <label class="mobile-model-toggle">
              <input id="mobileModelEmbeddingMultimodal" type="checkbox">
              <span>启用多模态探测</span>
            </label>
            <p class="mobile-settings-hint">所有 Provider 共享模型、维度、阈值和多模态设置。</p>
          </section>

          <div id="mobileModelRouteLayout" class="mobile-model-route-layout">
            <section id="mobileModelRouteListPane" class="mobile-model-route-list-pane"
              aria-label="模型路由列表">
              <div class="mobile-model-section-heading">
                <div><p class="eyebrow">Ordered route</p><h3 id="mobileModelRouteTitle">Chat connections</h3></div>
                <button id="mobileModelAddConnection" type="button">添加连接</button>
              </div>
              <p id="mobileModelRouteHelp" class="mobile-settings-hint"></p>
              <div id="mobileModelRouteList" class="mobile-model-route-list"
                role="list" aria-live="polite"></div>
            </section>

            <section id="mobileModelInspectorPane" class="mobile-model-inspector-pane"
              aria-label="模型连接详情">
              <button id="mobileModelInspectorBack" class="mobile-model-back"
                type="button">← 返回列表</button>
              <div id="mobileModelInspector" class="mobile-model-inspector">
                <div class="mobile-model-section-heading">
                  <div>
                    <p id="mobileModelInspectorRole" class="eyebrow">Primary</p>
                    <h3 id="mobileModelInspectorTitle">连接详情</h3>
                  </div>
                </div>
                <div class="mobile-model-order-actions" aria-label="调整优先级">
                  <button id="mobileModelMoveUp" type="button">Move Up</button>
                  <button id="mobileModelMoveDown" type="button">Move Down</button>
                </div>
                <div id="mobileModelInspectorFields"></div>
                <fieldset class="mobile-model-type-chooser">
                  <legend>连接类型</legend>
                  <label class="mobile-model-field">
                    <span>搜索连接类型</span>
                    <input id="mobileModelTypeSearch" type="search" autocomplete="off"
                      placeholder="协议、本地运行时或 OAuth">
                  </label>
                  <div id="mobileModelConnectionTypeGroups"
                    class="mobile-model-connection-type-groups" role="listbox"
                    aria-label="连接类型"></div>
                </fieldset>
                <div id="mobileModelDescriptorFields"></div>
                <section id="mobileModelCredentialEditor"
                  class="mobile-model-credential-editor" aria-label="凭据来源"></section>
                <div class="mobile-model-probe-row">
                  <button id="mobileModelProbeButton" type="button">测试当前连接</button>
                  <span id="mobileModelProbeStatus" aria-live="polite"></span>
                </div>
                <button id="mobileModelRemoveConnection" class="mobile-model-danger"
                  type="button">移除连接</button>
              </div>
            </section>
          </div>
        </section>

        <section id="mobileModelRuntimeView" class="mobile-model-runtime" hidden>
          <div class="mobile-model-section-heading">
            <div><p class="eyebrow">Route policy</p><h3>Runtime</h3></div>
          </div>
          <label class="mobile-model-field">
            <span>Chat 并发数</span>
            <input id="mobileModelChatConcurrency" type="number" min="1" max="16"
              inputmode="numeric">
          </label>
          <label class="mobile-model-field">
            <span>整条 route 超时（秒）</span>
            <input id="mobileModelChatTimeout" type="number" min="10" inputmode="numeric">
          </label>
          <div id="mobileModelRuntimeSummary" class="mobile-model-runtime-summary"
            aria-live="polite"></div>
        </section>

        <div class="mobile-model-save-bar">
          <span id="mobileModelSaveStatus" aria-live="polite">选择 Models 后加载配置。</span>
          <button id="mobileModelSaveButton" type="button">保存模型路由</button>
        </div>
      </fieldset>
    </section>`;
  overlay.append(card);
  document.body.append(overlay);

  const byId = (id) => card.querySelector(`#${CSS.escape(id)}`);
  const savedToggle = byId("mobile-saved-auto-sync");
  const savedSave = card.querySelector(".mobile-settings-save");
  const savedRetry = card.querySelector(".mobile-settings-retry");
  const savedStatus = card.querySelector(".mobile-settings-status");
  const closeButton = card.querySelector(".mobile-settings-close");
  let currentSettingsSection = "saved";
  let savedStoredValue = false;
  let configLoaded = false;
  let focusController = null;
  let state = null;
  let connectionTypes = { connection_types: [], groups: [] };
  let routeView = "list";
  let disposed = false;
  const modelOperations = createModelOperationGate();
  const snapshotRequestGate = createLatestRequestGate();
  const descriptorRequestGate = createLatestRequestGate();

  function setSavedStatus(message, alert = false) {
    if (disposed) return;
    savedStatus.textContent = message;
    if (alert) savedStatus.setAttribute("role", "alert");
    else savedStatus.removeAttribute("role");
  }

  async function loadSavedSync() {
    configLoaded = false;
    savedToggle.disabled = true;
    savedSave.disabled = true;
    savedRetry.hidden = true;
    setSavedStatus("正在加载设置…");
    try {
      const config = await fetchConfig();
      if (disposed) return;
      savedStoredValue = config.saved_sync?.auto_sync_enabled === true;
      savedToggle.checked = savedStoredValue;
      configLoaded = true;
      savedToggle.disabled = false;
      savedSave.disabled = false;
      setSavedStatus("设置已加载。");
    } catch (error) {
      if (disposed) return;
      savedRetry.hidden = false;
      setSavedStatus(error?.message || "配置加载失败，请稍后重试。", true);
    }
  }

  async function saveSavedSync() {
    if (!configLoaded || savedSave.disabled) return;
    savedSave.disabled = true;
    savedSave.textContent = "保存中…";
    setSavedStatus("正在保存设置…");
    try {
      await updateConfig(buildSavedSyncUpdate(savedToggle.checked));
      if (disposed) return;
      savedStoredValue = savedToggle.checked;
      setSavedStatus("设置已保存。手动同步始终可用。");
    } catch (error) {
      if (!disposed) setSavedStatus(error?.message || "设置保存失败，请重试。", true);
    } finally {
      if (!disposed) {
        savedSave.disabled = false;
        savedSave.textContent = "保存设置";
      }
    }
  }

  function activeItems() {
    if (!state || state.activeRoute === "runtime") return [];
    return state.activeRoute === "chat"
      ? state.models.chat.connections
      : state.models.embedding.providers;
  }

  function descriptorFor(typeId) {
    return connectionTypes.connection_types.find(
      (descriptor) => descriptor.id === typeId,
    ) || null;
  }

  function presetFor(descriptor, presetId) {
    return descriptor?.preset_definitions?.find((preset) => preset.id === presetId) || null;
  }

  function descriptorsFor(kind) {
    return connectionTypes.connection_types.filter(
      (descriptor) => descriptor.capabilities?.includes(kind),
    );
  }

  function selectedIndex() {
    const record = selectedRecord(state, state.activeRoute);
    return record ? activeItems().findIndex((item) => item.id === record.id) : -1;
  }

  function derivedRole(index) {
    return index === 0 ? "Primary" : `Fallback ${index}`;
  }

  function modelControlLocked(path) {
    if (!state) return null;
    return state.overrideLocks?.[path] || null;
  }

  function routeLocked(kind) {
    return kind === "chat" || kind === "embedding"
      ? modelControlLocked(ROUTE_OVERRIDE_PATHS[kind])
      : null;
  }

  function modelMutationBlocked() {
    return !state || modelOperations.saveInFlight;
  }

  function syncOperationControls() {
    const controls = modelOperations.controlState();
    const save = byId("mobileModelSaveButton");
    const probe = byId("mobileModelProbeButton");
    if (save) save.disabled = controls.saveDisabled || !state;
    if (probe) probe.disabled = controls.probeDisabled || !state;
  }

  function setModelEditorLocked(locked) {
    const boundary = byId("mobileModelEditorBoundary");
    if (!boundary) return;
    const editorLocked = Boolean(locked || modelOperations.saveInFlight);
    boundary.disabled = editorLocked;
    boundary.inert = editorLocked;
    boundary.setAttribute("aria-busy", editorLocked ? "true" : "false");
    syncOperationControls();
  }

  function setModelStatus(message, tone = "") {
    if (disposed) return;
    const status = byId("mobileModelSaveStatus");
    if (!status) return;
    status.textContent = message;
    if (tone) status.dataset.tone = tone;
    else delete status.dataset.tone;
  }

  function fieldError(recordId, field) {
    return state?.fieldErrors?.byConnection?.[recordId]?.[field] || null;
  }

  function errorMarkup(recordId, field) {
    const error = fieldError(recordId, field);
    return error
      ? `<span class="mobile-model-field-error" role="alert">${escapeHtml(error.message)}</span>`
      : "";
  }

  function safeHealth(record) {
    if (record?.circuit?.state === "open") {
      return { label: record.circuit.failure_kind || "Circuit open", tone: "error" };
    }
    if (record?.probe?.ok === true) return { label: "Probe passed", tone: "success" };
    if (record?.probe?.ok === false) {
      return { label: record.probe.error_code || "Probe failed", tone: "error" };
    }
    return { label: "Not probed", tone: "" };
  }

  function uniqueId(kind) {
    const token = window.crypto?.randomUUID?.()
      || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return `${kind}-${token}`;
  }

  function renderTabs() {
    card.querySelectorAll("[data-mobile-model-route]").forEach((tab) => {
      const active = tab.dataset.mobileModelRoute === state.activeRoute;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    const runtime = state.activeRoute === "runtime";
    byId("mobileModelRuntimeView").hidden = !runtime;
    card.querySelector('[data-mobile-model-view="route"]').hidden = runtime;
    byId("mobileModelEmbeddingSharedSettings").hidden = (
      state.activeRoute !== "embedding"
    );
  }

  function renderRemoteUpdate() {
    byId("mobileModelRemoteBanner").hidden = !state.remoteUpdate;
  }

  function renderOverrides() {
    const host = byId("mobileModelOverrideNotice");
    const overrides = state.overrides || [];
    host.hidden = overrides.length === 0;
    host.innerHTML = overrides.length
      ? `
        <strong>只读模型覆盖</strong>
        <p>高优先级配置锁定下列字段；其余基础配置仍可保存。</p>
        <ul>${overrides.map((override) => `
          <li><code>${escapeHtml(override.path)}</code>
            <span>${escapeHtml(override.source)}</span></li>`).join("")}</ul>`
      : "";
  }

  function renderErrorSummary() {
    const host = byId("mobileModelErrorSummary");
    const global = state.fieldErrors?.global || [];
    const connectionErrors = Object.entries(state.fieldErrors?.byConnection || {}).flatMap(
      ([connectionId, fields]) => Object.values(fields).map((error) => ({
        connectionId,
        error,
      })),
    );
    const rows = [
      ...global.map((error) => escapeHtml(error.message)),
      ...connectionErrors.map(
        ({ connectionId, error }) => (
          `${escapeHtml(connectionId)}: ${escapeHtml(error.message)}`
        ),
      ),
    ];
    // API validation entries are keyed by connection_id before the shared reducer maps them.
    host.hidden = rows.length === 0;
    host.innerHTML = rows.length ? `<ul><li>${rows.join("</li><li>")}</li></ul>` : "";
  }

  function renderEmbeddingSettings() {
    if (state.activeRoute !== "embedding") return;
    const settings = state.models.embedding.settings;
    byId("mobileModelEmbeddingEnabled").checked = state.models.embedding.enabled;
    byId("mobileModelEmbeddingEnabled").disabled = Boolean(
      modelControlLocked("models.embedding.enabled"),
    );
    byId("mobileModelEmbeddingModel").value = settings.model;
    byId("mobileModelEmbeddingModel").disabled = Boolean(
      modelControlLocked("models.embedding.settings.model"),
    );
    byId("mobileModelEmbeddingDimension").value = String(
      settings.output_dimensionality,
    );
    byId("mobileModelEmbeddingDimension").disabled = Boolean(
      modelControlLocked("models.embedding.settings.output_dimensionality"),
    );
    byId("mobileModelEmbeddingSimilarity").value = String(settings.similarity_threshold);
    byId("mobileModelEmbeddingSimilarity").disabled = Boolean(
      modelControlLocked("models.embedding.settings.similarity_threshold"),
    );
    byId("mobileModelEmbeddingMultimodal").checked = settings.multimodal_enabled;
    byId("mobileModelEmbeddingMultimodal").disabled = Boolean(
      modelControlLocked("models.embedding.settings.multimodal_enabled"),
    );
  }

  function renderRouteList() {
    if (state.activeRoute === "runtime") return;
    const kind = state.activeRoute;
    const items = activeItems();
    const locked = routeLocked(kind);
    byId("mobileModelRouteTitle").textContent = kind === "chat"
      ? "Chat connections"
      : "Embedding providers";
    byId("mobileModelRouteHelp").textContent = kind === "chat"
      ? "第 1 项是 Primary，其余项依序作为 fallback；最多 10 项。"
      : "Provider 按此顺序 fallback，并共享唯一 Embedding 模型设置；最多 10 项。";
    byId("mobileModelAddConnection").disabled = (
      Boolean(locked)
      || items.length >= MAX_ROUTE_ITEMS
      || (kind === "embedding" && !state.models.embedding.enabled)
    );
    byId("mobileModelRouteList").innerHTML = items.map((record, index) => {
      const descriptor = descriptorFor(record.type);
      const preset = presetFor(descriptor, record.preset);
      const health = safeHealth(record);
      const model = kind === "chat" ? record.model : state.models.embedding.settings.model;
      const selected = state.selected[kind] === record.id;
      return `
        <article class="mobile-model-route-row${selected ? " is-selected" : ""}"
          data-model-record-id="${escapeHtml(record.id)}" role="listitem">
          <button class="mobile-model-route-row-copy" type="button"
            data-model-select="${escapeHtml(record.id)}"
            aria-current="${selected ? "true" : "false"}">
            <strong>${escapeHtml(derivedRole(index))} ·
              ${escapeHtml(record.name || "Unnamed connection")}</strong>
            <span>${escapeHtml(descriptor?.label || record.type)}
              ${preset ? ` / ${escapeHtml(preset.label)}` : ""}
              · ${escapeHtml(model || "No model")}</span>
            <small class="mobile-model-route-health" data-tone="${health.tone}">
              ${escapeHtml(health.label)}</small>
          </button>
        </article>`;
    }).join("") || '<p class="mobile-settings-hint">当前 route 为空。</p>';
  }

  function renderConnectionTypes() {
    const record = selectedRecord(state, state.activeRoute);
    if (!record) return;
    const locked = Boolean(routeLocked(state.activeRoute));
    const query = String(byId("mobileModelTypeSearch")?.value || "").trim().toLowerCase();
    const host = byId("mobileModelConnectionTypeGroups");
    const blocks = [];
    for (const group of connectionTypes.groups) {
      const matches = group.connection_types.filter((descriptor) => {
        if (!descriptor.capabilities?.includes(state.activeRoute)) return false;
        const searchText = [
          descriptor.id,
          descriptor.label,
          descriptor.help,
          ...(descriptor.preset_definitions || []).map(
            (preset) => `${preset.id} ${preset.label}`,
          ),
        ].join(" ").toLowerCase();
        return !query || searchText.includes(query);
      });
      if (!matches.length) continue;
      blocks.push(`
        <section class="mobile-model-type-group"
          data-model-type-category="${escapeHtml(group.category)}">
          <p>${escapeHtml(CATEGORY_LABELS[group.category] || group.category)}</p>
          ${matches.map((descriptor) => `
            <button class="mobile-model-type-option" type="button" role="option"
              tabindex="-1" data-model-type="${escapeHtml(descriptor.id)}"
              aria-selected="${descriptor.id === record.type ? "true" : "false"}"
              ${disabledMarkup(locked)}>
              <span><strong>${escapeHtml(descriptor.label)}</strong>
                <small>${escapeHtml(descriptor.help)}</small></span>
              <small>${escapeHtml(
                descriptor.category === "oauth" ? "OAuth" : descriptor.id,
              )}</small>
            </button>`).join("")}
        </section>`);
    }
    host.innerHTML = blocks.join("")
      || '<p class="mobile-settings-hint">没有匹配的连接类型。</p>';
    const options = [...host.querySelectorAll('[role="option"]:not(:disabled)')];
    const selected = options.find((option) => option.getAttribute("aria-selected") === "true");
    const roving = selected || options[0];
    if (roving) roving.tabIndex = 0;
  }

  function moveTypeOptionFocus(event) {
    if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    const options = [
      ...event.currentTarget.querySelectorAll('[role="option"]:not(:disabled)'),
    ];
    if (!options.length) return;
    const current = event.target.closest('[role="option"]');
    let index = Math.max(0, options.indexOf(current));
    if (event.key === "Home") index = 0;
    else if (event.key === "End") index = options.length - 1;
    else if (event.key === "ArrowUp") index = Math.max(0, index - 1);
    else index = Math.min(options.length - 1, index + 1);
    event.preventDefault();
    for (const option of options) option.tabIndex = option === options[index] ? 0 : -1;
    options[index].focus();
  }

  function focusSelectedTypeOption() {
    const record = selectedRecord(state, state.activeRoute);
    if (!record) return;
    window.requestAnimationFrame(() => {
      byId("mobileModelConnectionTypeGroups")
        ?.querySelector(`[data-model-type="${CSS.escape(record.type)}"]`)
        ?.focus();
    });
  }

  function renderDescriptorField(record, descriptor, field) {
    if (field.name === "credential") return "";
    if (field.capabilities?.length && !field.capabilities.includes(state.activeRoute)) {
      return "";
    }
    if (field.presets?.length && !field.presets.includes(record.preset)) return "";
    const required = field.required ? " required" : "";
    const disabled = disabledMarkup(Boolean(routeLocked(state.activeRoute)));
    const help = field.help
      ? `<small>${escapeHtml(field.help)}</small>`
      : "";
    if (field.name === "preset") {
      const presets = (descriptor.preset_definitions || []).filter(
        (preset) => preset.capabilities?.includes(state.activeRoute),
      );
      return `<label class="mobile-model-field"><span>${escapeHtml(field.label)}</span>
        <select data-model-field="preset"${required}${disabled}>
          ${presets.map((preset) => `<option value="${escapeHtml(preset.id)}"
            ${preset.id === record.preset ? " selected" : ""}>
            ${escapeHtml(preset.label)}</option>`).join("")}
        </select>${help}${errorMarkup(record.id, "preset")}</label>`;
    }
    if (field.input_type === "select") {
      return `<label class="mobile-model-field"><span>${escapeHtml(field.label)}</span>
        <select data-model-field="${escapeHtml(field.name)}"${required}${disabled}>
          ${(field.choices || []).map((choice) => `<option
            value="${escapeHtml(choice)}"
            ${String(record[field.name] || "") === choice ? " selected" : ""}>
            ${escapeHtml(choice)}</option>`).join("")}
        </select>${help}${errorMarkup(record.id, field.name)}</label>`;
    }
    const type = field.input_type === "number" ? "number" : "text";
    return `<label class="mobile-model-field"><span>${escapeHtml(field.label)}</span>
      <input type="${type}" data-model-field="${escapeHtml(field.name)}"
        value="${escapeHtml(record[field.name] ?? "")}"
        placeholder="${escapeHtml(field.placeholder || "")}" autocomplete="off"
        ${required}${disabled}>
      ${help}${errorMarkup(record.id, field.name)}</label>`;
  }

  function renderCredential(record, descriptor) {
    const host = byId("mobileModelCredentialEditor");
    const definition = descriptor?.fields?.find((field) => field.name === "credential");
    if (!definition) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    host.hidden = false;
    const credential = record.credential;
    const status = credential.status || {};
    const disabled = disabledMarkup(Boolean(routeLocked(state.activeRoute)));
    if (descriptor.category === "oauth") {
      const importedReference = (
        status.credential_ref || definition.choices?.[0] || descriptor.label
      );
      host.innerHTML = `
        <strong>Imported OAuth credential</strong>
        <p>${status.oauth_logged_in ? "已登录" : "尚未检测到登录"} ·
          ${escapeHtml(importedReference)}</p>
        <input type="hidden" data-model-credential-action="keep" value="keep">
        ${errorMarkup(record.id, "credential")}`;
      return;
    }
    const actions = [
      ["keep", "Keep existing"],
      ["set", "Set API key"],
      ["env", "Environment variable"],
      ["clear", "Clear"],
    ];
    const sourceLabel = status.configured
      ? `Current source: ${status.source}${status.env_name ? ` (${status.env_name})` : ""}`
      : "No credential is currently configured.";
    const needsValue = credential.action === "set" || credential.action === "env";
    host.innerHTML = `
      <strong>Credential source</strong>
      <p>${escapeHtml(sourceLabel)}</p>
      <div class="mobile-model-credential-actions">
        ${actions.map(([action, label]) => `
          <button type="button" data-model-credential-action="${action}"
            aria-pressed="${credential.action === action ? "true" : "false"}"
            ${disabled}>${label}</button>`).join("")}
      </div>
      ${needsValue ? `
        <label class="mobile-model-field">
          <span>${credential.action === "env"
    ? "Environment variable name" : "New API key"}</span>
          <input id="mobileModelCredentialValue"
            type="${credential.action === "set" ? "password" : "text"}"
            value="${escapeHtml(credential.value || "")}"
            autocomplete="new-password" ${disabled}>
        </label>` : ""}
      ${errorMarkup(record.id, "credential")}`;
  }

  function renderProbeStatus(record) {
    const status = byId("mobileModelProbeStatus");
    if (!status) return;
    const probe = record?.probe;
    if (!probe) {
      status.textContent = "尚未探测此精确草稿。";
      delete status.dataset.tone;
      return;
    }
    const dimensions = probe.observed_dimension
      ? ` · ${probe.observed_dimension} dimensions`
      : "";
    const latency = probe.latency_ms ? ` · ${probe.latency_ms} ms` : "";
    const timestamp = probe.probed_at
      ? ` · ${new Date(probe.probed_at).toLocaleString()}`
      : "";
    status.textContent = `${probe.ok ? "通过" : probe.error_code || "失败"}${dimensions}${latency}${timestamp}`;
    status.dataset.tone = probe.ok ? "success" : "error";
  }

  function renderInspector() {
    if (state.activeRoute === "runtime") return;
    const kind = state.activeRoute;
    const record = selectedRecord(state, kind);
    const inspector = byId("mobileModelInspector");
    inspector.hidden = !record;
    if (!record) return;
    const index = selectedIndex();
    const descriptor = descriptorFor(record.type);
    const locked = Boolean(routeLocked(kind));
    byId("mobileModelInspectorRole").textContent = derivedRole(index);
    byId("mobileModelInspectorTitle").textContent = record.name || "连接详情";
    byId("mobileModelMoveUp").disabled = locked || index <= 0;
    byId("mobileModelMoveDown").disabled = (
      locked || index < 0 || index >= activeItems().length - 1
    );
    byId("mobileModelRemoveConnection").disabled = locked || (
      (kind === "chat" && activeItems().length <= 1)
      || (kind === "embedding" && state.models.embedding.enabled
        && activeItems().length <= 1)
    );
    byId("mobileModelTypeSearch").disabled = locked;
    byId("mobileModelInspectorFields").innerHTML = `
      <label class="mobile-model-field">
        <span>连接名称</span>
        <input data-model-field="name" value="${escapeHtml(record.name)}"
          autocomplete="off" required ${disabledMarkup(locked)}>
        ${errorMarkup(record.id, "name")}
      </label>
      <label class="mobile-model-field">
        <span>Stable ID</span>
        <input value="${escapeHtml(record.id)}" readonly aria-readonly="true">
        <small>排序或改名不会改变此 ID。</small>
        ${errorMarkup(record.id, "id")}
      </label>`;
    byId("mobileModelDescriptorFields").innerHTML = descriptor
      ? descriptor.fields.map(
        (field) => renderDescriptorField(record, descriptor, field),
      ).join("")
      : "";
    renderConnectionTypes();
    renderCredential(record, descriptor);
    renderProbeStatus(record);
  }

  function renderRuntime() {
    if (state.activeRoute !== "runtime") return;
    byId("mobileModelChatConcurrency").value = String(state.models.chat.concurrency);
    byId("mobileModelChatConcurrency").disabled = Boolean(
      modelControlLocked("models.chat.concurrency"),
    );
    byId("mobileModelChatTimeout").value = String(state.models.chat.timeout_seconds);
    byId("mobileModelChatTimeout").disabled = Boolean(
      modelControlLocked("models.chat.timeout_seconds"),
    );
    const all = [...state.models.chat.connections, ...state.models.embedding.providers];
    const open = all.filter((record) => record.circuit?.state === "open").length;
    const healthy = all.filter((record) => record.probe?.ok === true).length;
    byId("mobileModelRuntimeSummary").innerHTML = `
      <div><span>Chat route</span><strong>
        ${state.models.chat.connections.length} connections</strong></div>
      <div><span>Embedding route</span><strong>
        ${state.models.embedding.providers.length} providers ·
        ${state.models.embedding.enabled ? "enabled" : "disabled"}</strong></div>
      <div><span>Current health</span><strong>
        ${healthy} passed probes · ${open} open circuits</strong></div>`;
  }

  function migrationResolution(action) {
    if (action === "apply_shared_embedding_settings") {
      return { action, embedding_settings: { ...state.models.embedding.settings } };
    }
    return { action };
  }

  function renderMigration() {
    const panel = byId("mobileModelMigrationPanel");
    const issues = state.migration?.issues || [];
    panel.hidden = issues.length === 0;
    panel.innerHTML = issues.length
      ? `<h3>确认旧配置迁移</h3>
        ${issues.map((issue) => {
    const selected = state.migration_resolutions?.[issue.id]?.action || "";
    return `<article data-migration-issue="${escapeHtml(issue.id)}">
            <strong>${escapeHtml(issue.reason || issue.code)}</strong>
            <span>${escapeHtml(issue.field)}
              ${issue.provider ? ` · ${escapeHtml(issue.provider)}` : ""}</span>
            <div>${(issue.allowed_actions || []).map((action) => `
              <button type="button" data-migration-action="${escapeHtml(action)}"
                data-migration-id="${escapeHtml(issue.id)}"
                class="${selected === action ? "is-active" : ""}">
                ${escapeHtml(action.replaceAll("_", " "))}</button>`).join("")}</div>
          </article>`;
  }).join("")}`
      : "";
  }

  function render({ preserveStatus = false } = {}) {
    if (!state || disposed) return;
    renderTabs();
    renderRemoteUpdate();
    renderOverrides();
    renderErrorSummary();
    renderMigration();
    renderEmbeddingSettings();
    renderRouteList();
    renderInspector();
    renderRuntime();
    byId("mobileModelRouteLayout").classList.toggle("is-detail", routeView === "detail");
    syncOperationControls();
    if (!preserveStatus) {
      setModelStatus(
        state.dirty
          ? "有未保存的模型更改。"
          : `模型配置已同步 · ${state.revision.slice(0, 12)}`,
      );
    }
  }

  function focusSelectedRouteControl() {
    const record = selectedRecord(state, state.activeRoute);
    if (!record) return;
    window.requestAnimationFrame(() => {
      byId("mobileModelRouteList")
        ?.querySelector(`[data-model-select="${CSS.escape(record.id)}"]`)
        ?.focus();
    });
  }

  function focusDetailControl(id = "mobileModelInspectorBack") {
    window.requestAnimationFrame(() => byId(id)?.focus());
  }

  function showRouteList({ focus = true } = {}) {
    routeView = "list";
    byId("mobileModelRouteLayout")?.classList.remove("is-detail");
    if (focus) focusSelectedRouteControl();
  }

  function openRouteDetail() {
    routeView = "detail";
    byId("mobileModelRouteLayout")?.classList.add("is-detail");
    focusDetailControl();
  }

  function moveSelected(delta) {
    if (modelMutationBlocked() || routeLocked(state.activeRoute)) return;
    const record = selectedRecord(state, state.activeRoute);
    if (!record) return;
    state = moveRouteItem(
      state,
      state.activeRoute,
      record.id,
      selectedIndex() + delta,
    );
    render();
    focusDetailControl(delta < 0 ? "mobileModelMoveUp" : "mobileModelMoveDown");
  }

  function selectRecord(id) {
    if (modelMutationBlocked()) return;
    state = selectRouteItem(state, state.activeRoute, id);
    render({ preserveStatus: true });
    openRouteDetail();
  }

  function addConnection() {
    if (modelMutationBlocked() || routeLocked(state.activeRoute)) return;
    const descriptor = descriptorsFor(state.activeRoute)[0];
    if (!descriptor) {
      setModelStatus("当前 route 没有可用连接类型。", "error");
      return;
    }
    const preset = descriptor.preset_definitions?.find(
      (candidate) => candidate.capabilities?.includes(state.activeRoute),
    );
    const id = uniqueId(state.activeRoute);
    const record = {
      id,
      name: descriptor.label,
      type: descriptor.id,
      preset: preset?.id || "",
      base_url: "",
      credential: {
        action: descriptor.category === "oauth" ? "keep" : "clear",
        value: "",
      },
      ...(state.activeRoute === "chat" ? {
        model: "",
        api_mode: "",
        reasoning_effort: "",
        http_referer: "",
        x_title: "",
        num_ctx: 0,
      } : {}),
    };
    try {
      state = appendRouteItem(state, state.activeRoute, record);
      if (preset) state = applyPreset(state, state.activeRoute, id, preset);
      routeView = "detail";
      render();
      focusDetailControl();
    } catch (error) {
      setModelStatus(error.message, "error");
    }
  }

  function removeSelected() {
    if (modelMutationBlocked() || routeLocked(state.activeRoute)) return;
    const record = selectedRecord(state, state.activeRoute);
    if (!record || !window.confirm(`移除 ${record.name || record.id}？`)) return;
    try {
      state = removeRouteItem(state, state.activeRoute, record.id);
      routeView = "list";
      render();
      focusSelectedRouteControl();
    } catch (error) {
      setModelStatus(error.message, "error");
    }
  }

  function changeType(typeId) {
    if (modelMutationBlocked() || routeLocked(state.activeRoute)) return;
    const record = selectedRecord(state, state.activeRoute);
    const descriptor = descriptorFor(typeId);
    if (!record || !descriptor || record.type === typeId) return;
    const previousDescriptor = descriptorFor(record.type);
    const previousPreset = presetFor(previousDescriptor, record.preset);
    let result = changeConnectionType(state, state.activeRoute, record.id, descriptor, {
      confirmed: false,
      previousDescriptor,
    });
    if (result.incompatibleFields.length) {
      const confirmed = window.confirm(
        `切换连接类型会清除这些不兼容字段：${result.incompatibleFields.join(", ")}。继续吗？`,
      );
      if (!confirmed) return;
      result = changeConnectionType(state, state.activeRoute, record.id, descriptor, {
        confirmed: true,
        previousDescriptor,
      });
    }
    state = result.state;
    const updated = selectedRecord(state, state.activeRoute);
    const preset = descriptor.preset_definitions?.find(
      (candidate) => candidate.id === updated?.preset,
    );
    if (preset) {
      state = applyPreset(state, state.activeRoute, record.id, preset, { previousPreset });
    }
    render();
    focusSelectedTypeOption();
  }

  function updateField(field, target) {
    if (modelMutationBlocked() || routeLocked(state.activeRoute)) return;
    const record = selectedRecord(state, state.activeRoute);
    if (!record) return;
    let value = target.value;
    if (target.type === "number") value = Number(target.value);
    if (field === "preset") {
      const descriptor = descriptorFor(record.type);
      const preset = presetFor(descriptor, value);
      if (preset) {
        let result = changePreset(state, state.activeRoute, record.id, descriptor, preset, {
          confirmed: false,
        });
        if (result.incompatibleFields.length) {
          const confirmed = window.confirm(
            `切换 preset 会清除这些不兼容字段：${result.incompatibleFields.join(", ")}。继续吗？`,
          );
          if (!confirmed) {
            renderInspector();
            return;
          }
          result = changePreset(
            state,
            state.activeRoute,
            record.id,
            descriptor,
            preset,
            { confirmed: true },
          );
        }
        state = result.state;
      }
      renderInspector();
    } else {
      state = updateRouteField(state, state.activeRoute, record.id, field, value);
      if (field === "name") {
        byId("mobileModelInspectorTitle").textContent = value || "连接详情";
        renderRouteList();
      }
    }
    setModelStatus("有未保存的模型更改。");
  }

  function updateCredential(action, value = "", rerender = true) {
    if (modelMutationBlocked() || routeLocked(state.activeRoute)) return;
    const record = selectedRecord(state, state.activeRoute);
    if (!record) return;
    state = updateRouteField(
      state,
      state.activeRoute,
      record.id,
      "credential",
      { action, value },
    );
    if (rerender) {
      renderCredential(selectedRecord(state, state.activeRoute), descriptorFor(record.type));
    }
    setModelStatus("有未保存的模型更改。");
  }

  function probeRequestVisible(signature) {
    return Boolean(
      state
      && state.activeRoute === signature.kind
      && state.selected?.[signature.kind] === signature.id,
    );
  }

  async function probeSelected() {
    if (!state || modelOperations.saveInFlight || modelOperations.probeInFlight) return;
    const kind = state.activeRoute;
    const record = selectedRecord(state, kind);
    if (!record || kind === "runtime") return;
    const generation = modelOperations.beginProbe();
    const signature = createProbeSignature(state, kind, record.id);
    if (!signature.fingerprint) return;
    const payload = toModelConfigPayload(state);
    const selectedDraft = signature.kind === "chat"
      ? payload.models.chat.connections.find((item) => item.id === signature.id)
      : payload.models.embedding.providers.find((item) => item.id === signature.id);
    const body = signature.kind === "chat"
      ? {
        kind: signature.kind,
        revision: signature.revision,
        connection: selectedDraft,
      }
      : {
        kind: signature.kind,
        revision: signature.revision,
        provider: selectedDraft,
        settings: payload.models.embedding.settings,
      };
    syncOperationControls();
    const status = byId("mobileModelProbeStatus");
    status.textContent = "正在探测精确草稿…";
    delete status.dataset.tone;
    const started = globalThis.performance?.now?.() ?? Date.now();
    try {
      const result = await probeModelConnection(body);
      if (disposed || !modelOperations.isProbeCurrent(generation)) return;
      const finished = globalThis.performance?.now?.() ?? Date.now();
      const applied = applyProbeResult(state, signature, {
        ...result,
        observed_dimension: result.observed_dimension,
        probed_at: result.probed_at,
        latency_ms: Math.round(finished - started),
      });
      state = applied.state;
      renderRouteList();
      if (probeRequestVisible(signature)) {
        renderProbeStatus(selectedRecord(state, signature.kind));
      }
    } catch (error) {
      if (disposed || !modelOperations.isProbeCurrent(generation)) return;
      if (error.status === 409 && error.details?.latest) {
        state = receiveRemoteSnapshot(state, error.details.latest);
        render({ preserveStatus: true });
      }
      if (probeRequestVisible(signature)) {
        if (probeSignatureMatches(state, signature)) {
          status.textContent = error.details?.error || error.message || "Probe failed";
          status.dataset.tone = "error";
        } else {
          renderProbeStatus(selectedRecord(state, signature.kind));
        }
      }
    } finally {
      modelOperations.finishProbe(generation);
      if (!disposed) syncOperationControls();
    }
  }

  function retainSelection(next, previous) {
    for (const kind of ["chat", "embedding"]) {
      const id = previous?.selected?.[kind];
      const items = kind === "chat"
        ? next.models.chat.connections
        : next.models.embedding.providers;
      if (id && items.some((item) => item.id === id)) next.selected[kind] = id;
    }
    next.activeRoute = previous?.activeRoute || "chat";
    return next;
  }

  async function saveModels() {
    if (!state) return;
    const save = modelOperations.beginSave();
    if (!save) return;
    snapshotRequestGate.invalidate();
    setModelEditorLocked(true);
    byId("mobileModelSaveButton").textContent = "保存中…";
    if (save.invalidatedProbe) {
      renderProbeStatus(selectedRecord(state, state.activeRoute));
    }
    setModelStatus("正在验证并热重载模型 route…");
    try {
      const result = await updateModelConfig(toModelConfigPayload(state));
      if (disposed) return;
      state = retainSelection(hydrateModelConfig(result.snapshot), state);
      render({ preserveStatus: true });
      setModelStatus("模型 route 已保存并热重载。", "success");
    } catch (error) {
      if (disposed) return;
      if (error.status === 409 && error.details?.error === "revision_conflict") {
        state = receiveRemoteSnapshot(state, error.details.latest);
        render({ preserveStatus: true });
        setModelStatus("保存被拒绝：远端已有更新，当前安全草稿仍保留。", "error");
      } else if (Array.isArray(error.details?.errors)) {
        state = mapServerFieldErrors(state, error.details.errors);
        render({ preserveStatus: true });
        setModelStatus("请修正标记的模型字段；当前草稿尚未丢失。", "error");
      } else {
        setModelStatus(error.details?.error || error.message || "模型保存失败。", "error");
      }
    } finally {
      if (modelOperations.finishSave(save.generation)) {
        if (!disposed) {
          byId("mobileModelSaveButton").textContent = "保存模型路由";
          setModelEditorLocked(false);
        }
      }
    }
  }

  async function fetchModelSnapshot(remote = false) {
    if (modelOperations.saveInFlight) return false;
    return applyLatestSnapshotRequest({
      gate: snapshotRequestGate,
      request: () => fetchModelConfig(),
      blocked: () => (
        disposed
        || modelOperations.saveInFlight
        || (!remote && Boolean(state?.dirty))
      ),
      onBlocked: (snapshot) => {
        if (disposed || !state) return;
        state = receiveRemoteSnapshot(state, snapshot);
        render({ preserveStatus: true });
      },
      apply: (snapshot) => {
        if (disposed) return;
        if (remote && state) state = receiveRemoteSnapshot(state, snapshot);
        else state = retainSelection(hydrateModelConfig(snapshot), state);
        render({ preserveStatus: true });
      },
    });
  }

  async function loadModelSettings() {
    setModelStatus("正在读取模型配置与连接类型…");
    try {
      const loaded = await loadIndependentModelResources({
        gate: snapshotRequestGate,
        descriptorGate: descriptorRequestGate,
        snapshotRequest: () => fetchModelConfig(),
        descriptorRequest: () => fetchModelConnectionTypes(),
        blocked: () => (
          disposed || modelOperations.saveInFlight || Boolean(state?.dirty)
        ),
        onSnapshotBlocked: (snapshot) => {
          if (disposed || !state) return;
          state = receiveRemoteSnapshot(state, snapshot);
          render({ preserveStatus: true });
        },
        applySnapshot: (snapshot) => {
          if (disposed) return;
          state = retainSelection(hydrateModelConfig(snapshot), state);
          state.activeRoute ||= "chat";
          routeView = "list";
          render({ preserveStatus: true });
        },
        installDescriptors: (descriptors) => {
          if (disposed) return;
          connectionTypes = descriptors;
          if (state && !modelOperations.saveInFlight) {
            render({ preserveStatus: true });
          }
        },
      });
      if (disposed) return loaded;
      if (loaded.snapshotApplied && loaded.descriptorsInstalled && state) {
        setModelStatus(`模型配置已同步 · ${state.revision.slice(0, 12)}`);
      } else if (state?.remoteUpdate) {
        setModelStatus("远端已有更新；本地未保存草稿仍保留。");
      } else if (state?.dirty) {
        setModelStatus("模型配置已读取；本地草稿尚未保存。");
      }
      return loaded;
    } catch (error) {
      if (disposed) return { snapshotApplied: false, descriptorsInstalled: false };
      setModelStatus(error.message || "无法读取模型配置。", "error");
      return { snapshotApplied: false, descriptorsInstalled: false };
    }
  }

  function confirmLeave() {
    return !state?.dirty || window.confirm("模型 route 有未保存的更改，确定离开吗？");
  }

  function destroy({ restoreFocus = true } = {}) {
    if (disposed) return;
    disposed = true;
    snapshotRequestGate.invalidate();
    descriptorRequestGate.invalidate();
    window.removeEventListener("beforeunload", onBeforeUnload);
    window.removeEventListener("openbiliclaw:config-reloaded", onConfigReloaded);
    if (restoreFocus) focusController?.deactivate();
    overlay.remove();
    if (requestCloseActiveSettings === requestClose) {
      requestCloseActiveSettings = null;
    }
  }

  function requestClose() {
    if (!confirmLeave()) return false;
    destroy();
    return true;
  }

  function switchSettingsSection(nextSection, trigger = null) {
    if (nextSection === currentSettingsSection) return true;
    if (
      currentSettingsSection === "models"
      && nextSection !== "models"
      && !confirmLeave()
    ) return false;
    currentSettingsSection = nextSection;
    card.querySelectorAll("[data-mobile-settings-section]").forEach((tab) => {
      const active = tab.dataset.mobileSettingsSection === nextSection;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    card.querySelectorAll("[data-mobile-settings-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.mobileSettingsPanel !== nextSection;
    });
    if (nextSection === "models" && !state) void loadModelSettings();
    window.requestAnimationFrame(() => {
      if (disposed) return;
      const panel = card.querySelector(
        `[data-mobile-settings-panel="${CSS.escape(nextSection)}"]`,
      );
      panel?.querySelector(".mobile-settings-panel-title")?.focus();
      if (!panel && trigger) trigger.focus();
    });
    return true;
  }

  function onBeforeUnload(event) {
    if (!state?.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  }

  function onConfigReloaded(event) {
    if (event.detail?.type && event.detail.type !== CONFIG_RELOADED_TYPE) return;
    if (modelOperations.saveInFlight) return;
    void fetchModelSnapshot(true).catch(() => {});
  }

  card.querySelectorAll("[data-mobile-settings-section]").forEach((tab) => {
    tab.addEventListener("click", () => {
      switchSettingsSection(tab.dataset.mobileSettingsSection, tab);
    });
  });
  closeButton.addEventListener("click", requestClose);
  savedRetry.addEventListener("click", () => { void loadSavedSync(); });
  savedSave.addEventListener("click", () => { void saveSavedSync(); });
  savedToggle.addEventListener("change", () => {
    if (!savedToggle.checked || savedStoredValue) return;
    const warning = "开启后，在 OpenBiliClaw 点击收藏或稍后再看会修改对应平台账号中的收藏、书签、Saved、播放列表或稍后观看。";
    if (!window.confirm(warning)) {
      savedToggle.checked = false;
      setSavedStatus("已取消，自动同步仍为关闭。");
    }
  });

  card.querySelectorAll("[data-mobile-model-route]").forEach((tab) => {
    tab.addEventListener("click", () => {
      if (modelMutationBlocked()) return;
      state.activeRoute = tab.dataset.mobileModelRoute;
      routeView = "list";
      render();
    });
  });
  byId("mobileModelAddConnection").addEventListener("click", addConnection);
  byId("mobileModelRemoveConnection").addEventListener("click", removeSelected);
  byId("mobileModelMoveUp").addEventListener("click", () => moveSelected(-1));
  byId("mobileModelMoveDown").addEventListener("click", () => moveSelected(1));
  byId("mobileModelInspectorBack").addEventListener("click", () => {
    showRouteList();
  });
  byId("mobileModelSaveButton").addEventListener("click", () => void saveModels());
  byId("mobileModelProbeButton").addEventListener("click", () => void probeSelected());
  byId("mobileModelReloadRemote").addEventListener("click", () => {
    if (modelMutationBlocked() || !state?.remoteUpdate) return;
    if (!window.confirm("放弃当前草稿并加载远端模型配置？")) return;
    state = retainSelection(hydrateModelConfig(state.remoteUpdate.snapshot), state);
    routeView = "list";
    render();
  });
  byId("mobileModelTypeSearch").addEventListener("input", renderConnectionTypes);
  byId("mobileModelConnectionTypeGroups").addEventListener("click", (event) => {
    const button = event.target.closest("[data-model-type]");
    if (button) changeType(button.dataset.modelType);
  });
  byId("mobileModelConnectionTypeGroups").addEventListener(
    "keydown",
    moveTypeOptionFocus,
  );
  byId("mobileModelInspectorFields").addEventListener("input", (event) => {
    const field = event.target.dataset.modelField;
    if (field) updateField(field, event.target);
  });
  byId("mobileModelDescriptorFields").addEventListener("input", (event) => {
    const field = event.target.dataset.modelField;
    if (field && event.target.tagName !== "SELECT") updateField(field, event.target);
  });
  byId("mobileModelDescriptorFields").addEventListener("change", (event) => {
    const field = event.target.dataset.modelField;
    if (field) updateField(field, event.target);
  });
  byId("mobileModelCredentialEditor").addEventListener("click", (event) => {
    const action = event.target.closest("[data-model-credential-action]")
      ?.dataset.modelCredentialAction;
    if (action) updateCredential(action);
  });
  byId("mobileModelCredentialEditor").addEventListener("input", (event) => {
    if (event.target.id !== "mobileModelCredentialValue") return;
    const record = selectedRecord(state, state.activeRoute);
    updateCredential(record.credential.action, event.target.value, false);
  });
  byId("mobileModelRouteList").addEventListener("click", (event) => {
    const id = event.target.closest("[data-model-select]")?.dataset.modelSelect;
    if (id) selectRecord(id);
  });
  byId("mobileModelEmbeddingEnabled").addEventListener("change", (event) => {
    if (modelMutationBlocked()) return;
    if (modelControlLocked("models.embedding.enabled")) {
      renderEmbeddingSettings();
      return;
    }
    const providersLocked = Boolean(routeLocked("embedding"));
    if (!event.target.checked && state.models.embedding.providers.length
      && !providersLocked) {
      if (!window.confirm("停用 Embedding 会清空当前 Provider route。继续吗？")) {
        event.target.checked = true;
        return;
      }
      state = updateRouteSetting(state, "embedding", "enabled", false);
      state.models.embedding.providers = [];
      state.selected.embedding = "";
      render();
      return;
    }
    state = updateRouteSetting(state, "embedding", "enabled", event.target.checked);
    if (
      event.target.checked
      && state.models.embedding.providers.length === 0
      && !providersLocked
    ) {
      addConnection();
      return;
    }
    render();
  });
  for (const [id, field, kind, path] of [
    ["mobileModelEmbeddingModel", "model", "text", "models.embedding.settings.model"],
    [
      "mobileModelEmbeddingDimension",
      "output_dimensionality",
      "number",
      "models.embedding.settings.output_dimensionality",
    ],
    [
      "mobileModelEmbeddingSimilarity",
      "similarity_threshold",
      "number",
      "models.embedding.settings.similarity_threshold",
    ],
  ]) {
    byId(id).addEventListener("input", (event) => {
      if (modelMutationBlocked() || modelControlLocked(path)) return;
      const value = kind === "number" ? Number(event.target.value) : event.target.value;
      state = updateRouteSetting(state, "embedding", field, value);
      setModelStatus("有未保存的模型更改。");
    });
  }
  byId("mobileModelEmbeddingMultimodal").addEventListener("change", (event) => {
    if (
      modelMutationBlocked()
      || modelControlLocked("models.embedding.settings.multimodal_enabled")
    ) return;
    state = updateRouteSetting(
      state,
      "embedding",
      "multimodal_enabled",
      event.target.checked,
    );
    setModelStatus("有未保存的模型更改。");
  });
  byId("mobileModelChatConcurrency").addEventListener("input", (event) => {
    if (modelMutationBlocked() || modelControlLocked("models.chat.concurrency")) return;
    state = updateRouteSetting(
      state,
      "chat",
      "concurrency",
      Number(event.target.value),
    );
    setModelStatus("有未保存的模型更改。");
  });
  byId("mobileModelChatTimeout").addEventListener("input", (event) => {
    if (
      modelMutationBlocked()
      || modelControlLocked("models.chat.timeout_seconds")
    ) return;
    state = updateRouteSetting(
      state,
      "chat",
      "timeout_seconds",
      Number(event.target.value),
    );
    setModelStatus("有未保存的模型更改。");
  });
  byId("mobileModelMigrationPanel").addEventListener("click", (event) => {
    if (modelMutationBlocked()) return;
    const button = event.target.closest("[data-migration-action]");
    if (!button) return;
    state = setMigrationResolution(
      state,
      button.dataset.migrationId,
      migrationResolution(button.dataset.migrationAction),
    );
    renderMigration();
    setModelStatus("迁移选择尚未保存。");
  });

  focusController = createDialogFocusController({
    dialog: overlay,
    opener,
    onClose: requestClose,
  });
  focusController.activate();
  window.addEventListener("beforeunload", onBeforeUnload);
  window.addEventListener("openbiliclaw:config-reloaded", onConfigReloaded);
  requestCloseActiveSettings = requestClose;
  closeButton.focus();
  void loadSavedSync();
  return { requestClose, switchSettingsSection };
}
