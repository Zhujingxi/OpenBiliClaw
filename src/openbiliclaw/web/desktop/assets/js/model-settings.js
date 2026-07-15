const sharedStateUrl = new URL("/web/shared/model-config-state.js", import.meta.url);
const sharedStateVersion = new URL(import.meta.url).searchParams.get("v");
if (sharedStateVersion) sharedStateUrl.searchParams.set("v", sharedStateVersion);

const {
  appendRouteItem,
  applyPreset,
  changeConnectionType,
  changePreset,
  hydrateModelConfig,
  mapServerFieldErrors,
  moveRouteItem,
  receiveRemoteSnapshot,
  removeRouteItem,
  selectRouteItem,
  selectedRecord,
  setMigrationResolution,
  toModelConfigPayload,
  updateRouteField,
  updateRouteSetting,
} = await import(sharedStateUrl.href);

const MODEL_CONFIG_API = "/api/model-config";
const CONNECTION_TYPES_API = "/api/model-connection-types";
const MODEL_PROBE_API = "/api/model-config/probe";
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

let state = null;
let connectionTypes = { connection_types: [], groups: [] };
let draggedId = "";

const byId = (id) => document.getElementById(id);
const disabledMarkup = (disabled) => (disabled ? ' disabled aria-disabled="true"' : "");
const escapeHtml = (value) => String(value ?? "").replace(
  /[&<>'"]/g,
  (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[character],
);

function modelControlLocked(path) {
  if (!state) return null;
  return state.overrideLocks?.[path] || null;
}

function routeLocked(kind) {
  return kind === "chat" || kind === "embedding"
    ? modelControlLocked(ROUTE_OVERRIDE_PATHS[kind])
    : null;
}

function modelApiPath(url) {
  return url.startsWith("/api/") ? url.slice(4) : url;
}

async function requestModelJson(url, options = {}) {
  const bridge = window.OpenBiliClawDesktopApi;
  if (bridge?.requestJsonStrict) {
    return bridge.requestJsonStrict(modelApiPath(url), options);
  }
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), options.timeoutMs || 60000);
  try {
    const response = await fetch(url, {
      ...options,
      credentials: "same-origin",
      headers: { "X-OBC-Auth": "1", ...(options.headers || {}) },
      signal: controller.signal,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const error = new Error(data?.message || data?.detail || `HTTP ${response.status}`);
      error.status = response.status;
      error.details = data;
      throw error;
    }
    return data;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function showToast(message) {
  if (window.OpenBiliClawDesktopApi?.showToast) {
    window.OpenBiliClawDesktopApi.showToast(message);
  }
}

function activeItems() {
  if (!state || state.activeRoute === "runtime") return [];
  return state.activeRoute === "chat"
    ? state.models.chat.connections
    : state.models.embedding.providers;
}

function descriptorFor(typeId) {
  return connectionTypes.connection_types.find((descriptor) => descriptor.id === typeId) || null;
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

function fieldError(recordId, field) {
  return state?.fieldErrors?.byConnection?.[recordId]?.[field] || null;
}

function errorMarkup(recordId, field) {
  const error = fieldError(recordId, field);
  return error ? `<span class="model-field-error" role="alert">${escapeHtml(error.message)}</span>` : "";
}

function uniqueId(kind) {
  const token = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${kind}-${token}`;
}

function setStatus(message, tone = "") {
  const element = byId("modelSaveStatus");
  if (!element) return;
  element.textContent = message;
  if (tone) element.dataset.tone = tone;
  else delete element.dataset.tone;
}

function safeHealth(record) {
  if (record?.circuit?.state === "open") {
    return { label: record.circuit.failure_kind || "Circuit open", tone: "error" };
  }
  if (record?.probe?.ok === true) return { label: "Probe passed", tone: "success" };
  if (record?.probe?.ok === false) return { label: record.probe.error_code || "Probe failed", tone: "error" };
  return { label: "Not probed", tone: "" };
}

function renderTabs() {
  document.querySelectorAll("[data-model-route]").forEach((tab) => {
    const active = tab.dataset.modelRoute === state.activeRoute;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  const runtime = state.activeRoute === "runtime";
  byId("modelRuntimeView").hidden = !runtime;
  document.querySelector('[data-model-view="route"]').hidden = runtime;
  byId("modelEmbeddingSharedSettings").hidden = state.activeRoute !== "embedding";
}

function renderRemoteUpdate() {
  byId("modelRemoteBanner").hidden = !state.remoteUpdate;
}

function renderOverrides() {
  const host = byId("modelOverrideNotice");
  const overrides = state.overrides || [];
  host.hidden = overrides.length === 0;
  host.innerHTML = overrides.length ? `
    <strong>只读模型覆盖</strong>
    <p>以下字段由高优先级配置提供；对应编辑器已锁定，其余基础配置仍可保存。</p>
    <ul>${overrides.map((override) => `
      <li><code>${escapeHtml(override.path)}</code><span>${escapeHtml(override.source)}</span></li>`).join("")}</ul>` : "";
}

function renderErrorSummary() {
  const summary = byId("modelErrorSummary");
  const global = state.fieldErrors?.global || [];
  const connectionErrors = Object.entries(state.fieldErrors?.byConnection || {}).flatMap(
    ([id, fields]) => Object.values(fields).map((error) => `${id}: ${error.message}`),
  );
  const lines = [...global.map((error) => error.message), ...connectionErrors];
  summary.hidden = lines.length === 0;
  summary.textContent = lines.join("\n");
}

function renderEmbeddingSettings() {
  if (state.activeRoute !== "embedding") return;
  byId("modelEmbeddingEnabled").checked = state.models.embedding.enabled;
  byId("modelEmbeddingEnabled").disabled = Boolean(
    modelControlLocked("models.embedding.enabled"),
  );
  byId("modelEmbeddingModel").value = state.models.embedding.settings.model;
  byId("modelEmbeddingModel").disabled = Boolean(
    modelControlLocked("models.embedding.settings.model"),
  );
  byId("modelEmbeddingDimension").value = String(
    state.models.embedding.settings.output_dimensionality,
  );
  byId("modelEmbeddingDimension").disabled = Boolean(
    modelControlLocked("models.embedding.settings.output_dimensionality"),
  );
  byId("modelEmbeddingSimilarity").value = String(
    state.models.embedding.settings.similarity_threshold,
  );
  byId("modelEmbeddingSimilarity").disabled = Boolean(
    modelControlLocked("models.embedding.settings.similarity_threshold"),
  );
  byId("modelEmbeddingMultimodal").checked = state.models.embedding.settings.multimodal_enabled;
  byId("modelEmbeddingMultimodal").disabled = Boolean(
    modelControlLocked("models.embedding.settings.multimodal_enabled"),
  );
}

function renderRouteList() {
  if (state.activeRoute === "runtime") return;
  const kind = state.activeRoute;
  const items = activeItems();
  const locked = routeLocked(kind);
  byId("modelRouteTitle").textContent = kind === "chat" ? "Chat connections" : "Embedding providers";
  byId("modelRouteHelp").textContent = kind === "chat"
    ? "第 1 项是 Primary，其余项按顺序作为 fallback；最多 10 项。"
    : "所有 Provider 按此顺序 fallback，并共享上方唯一模型设置；最多 10 项。";
  byId("modelAddConnection").disabled = Boolean(locked) || items.length >= 10 || (
    kind === "embedding" && !state.models.embedding.enabled
  );
  byId("modelRouteList").innerHTML = items.map((record, index) => {
    const descriptor = descriptorFor(record.type);
    const preset = presetFor(descriptor, record.preset);
    const health = safeHealth(record);
    const model = kind === "chat" ? record.model : state.models.embedding.settings.model;
    const selected = state.selected[kind] === record.id;
    return `
      <div class="model-route-row${selected ? " is-selected" : ""}" draggable="${locked ? "false" : "true"}" data-model-record-id="${escapeHtml(record.id)}" tabindex="${selected ? "0" : "-1"}" aria-current="${selected ? "true" : "false"}">
        <span class="model-route-drag-handle" aria-label="Drag to reorder" title="${locked ? "Order is provided by a read-only override" : "Drag to reorder"}" aria-disabled="${locked ? "true" : "false"}">⋮⋮</span>
        <button class="model-route-row-copy" type="button" data-model-select="${escapeHtml(record.id)}">
          <strong>${escapeHtml(derivedRole(index))} · ${escapeHtml(record.name || "Unnamed connection")}</strong>
          <span>${escapeHtml(descriptor?.label || record.type)}${preset ? ` / ${escapeHtml(preset.label)}` : ""} · ${escapeHtml(model || "No model")}</span>
        </button>
        <span class="model-route-health" data-tone="${health.tone}">${escapeHtml(health.label)}</span>
      </div>`;
  }).join("") || '<p class="settings-note-inline">当前 route 为空。</p>';
}

function renderConnectionTypes() {
  const record = selectedRecord(state, state.activeRoute);
  if (!record) return;
  const locked = Boolean(routeLocked(state.activeRoute));
  const query = String(byId("modelTypeSearch")?.value || "").trim().toLowerCase();
  const host = byId("modelConnectionTypeGroups");
  const blocks = [];
  for (const group of connectionTypes.groups) {
    const matches = group.connection_types.filter((descriptor) => {
      if (!descriptor.capabilities?.includes(state.activeRoute)) return false;
      const searchText = [
        descriptor.id,
        descriptor.label,
        descriptor.help,
        ...(descriptor.preset_definitions || []).map((preset) => `${preset.id} ${preset.label}`),
      ].join(" ").toLowerCase();
      return !query || searchText.includes(query);
    });
    if (!matches.length) continue;
    blocks.push(`
      <section class="model-type-group" data-model-type-category="${escapeHtml(group.category)}">
        <p class="model-type-group-title">${escapeHtml(CATEGORY_LABELS[group.category] || group.category)}</p>
        ${matches.map((descriptor) => `
          <button class="model-type-option" type="button" role="option" tabindex="-1" data-model-type="${escapeHtml(descriptor.id)}" aria-selected="${descriptor.id === record.type ? "true" : "false"}"${disabledMarkup(locked)}>
            <span><strong>${escapeHtml(descriptor.label)}</strong><small>${escapeHtml(descriptor.help)}</small></span>
            <small>${escapeHtml(descriptor.category === "oauth" ? "OAuth" : descriptor.id)}</small>
          </button>`).join("")}
      </section>`);
  }
  host.innerHTML = blocks.join("") || '<p class="model-empty-types">没有匹配的连接类型。</p>';
  const options = [...host.querySelectorAll('[role="option"]:not(:disabled)')];
  const selected = options.find((option) => option.getAttribute("aria-selected") === "true");
  const roving = selected || options[0];
  if (roving) roving.tabIndex = 0;
}

function moveTypeOptionFocus(event) {
  if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
  const options = [...event.currentTarget.querySelectorAll('[role="option"]:not(:disabled)')];
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

function renderDescriptorField(record, descriptor, field) {
  if (field.name === "credential") return "";
  if (field.capabilities?.length && !field.capabilities.includes(state.activeRoute)) return "";
  if (field.presets?.length && !field.presets.includes(record.preset)) return "";
  const required = field.required ? " required" : "";
  const disabled = disabledMarkup(Boolean(routeLocked(state.activeRoute)));
  const help = field.help ? `<small>${escapeHtml(field.help)}</small>` : "";
  if (field.name === "preset") {
    const presets = (descriptor.preset_definitions || []).filter(
      (preset) => preset.capabilities?.includes(state.activeRoute),
    );
    return `<label class="settings-field"><span>${escapeHtml(field.label)}</span>
      <select data-model-field="preset"${required}${disabled}>${presets.map((preset) => `<option value="${escapeHtml(preset.id)}"${preset.id === record.preset ? " selected" : ""}>${escapeHtml(preset.label)}</option>`).join("")}</select>
      ${help}${errorMarkup(record.id, "preset")}</label>`;
  }
  if (field.input_type === "select") {
    return `<label class="settings-field"><span>${escapeHtml(field.label)}</span>
      <select data-model-field="${escapeHtml(field.name)}"${required}${disabled}>${(field.choices || []).map((choice) => `<option value="${escapeHtml(choice)}"${String(record[field.name] || "") === choice ? " selected" : ""}>${escapeHtml(choice)}</option>`).join("")}</select>
      ${help}${errorMarkup(record.id, field.name)}</label>`;
  }
  const type = field.input_type === "number" ? "number" : "text";
  return `<label class="settings-field"><span>${escapeHtml(field.label)}</span>
    <input type="${type}" data-model-field="${escapeHtml(field.name)}" value="${escapeHtml(record[field.name] ?? "")}" placeholder="${escapeHtml(field.placeholder || "")}" autocomplete="off"${required}${disabled}>
    ${help}${errorMarkup(record.id, field.name)}</label>`;
}

function renderCredential(record, descriptor) {
  const host = byId("modelCredentialEditor");
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
    const importedReference = status.credential_ref || definition.choices?.[0] || descriptor.label;
    host.innerHTML = `
      <strong>Imported OAuth credential</strong>
      <p class="settings-note-inline">${status.oauth_logged_in ? "已登录" : "尚未检测到登录"} · ${escapeHtml(importedReference)}</p>
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
    <p class="settings-note-inline">${escapeHtml(sourceLabel)}</p>
    <div class="model-credential-actions">${actions.map(([action, label]) => `<button class="model-credential-action" type="button" data-model-credential-action="${action}" aria-pressed="${credential.action === action ? "true" : "false"}"${disabled}>${label}</button>`).join("")}</div>
    ${needsValue ? `<label class="settings-field"><span>${credential.action === "env" ? "Environment variable name" : "New API key"}</span><input id="modelCredentialValue" type="${credential.action === "set" ? "password" : "text"}" value="${escapeHtml(credential.value || "")}" autocomplete="new-password"${disabled}></label>` : ""}
    ${errorMarkup(record.id, "credential")}`;
}

function renderInspector() {
  if (state.activeRoute === "runtime") return;
  const kind = state.activeRoute;
  const record = selectedRecord(state, kind);
  const inspector = byId("modelInspector");
  inspector.hidden = !record;
  if (!record) return;
  const index = selectedIndex();
  const descriptor = descriptorFor(record.type);
  const locked = Boolean(routeLocked(kind));
  byId("modelInspectorRole").textContent = derivedRole(index);
  byId("modelInspectorTitle").textContent = record.name || "连接详情";
  byId("modelMoveUp").disabled = locked || index <= 0;
  byId("modelMoveDown").disabled = locked || index < 0 || index >= activeItems().length - 1;
  byId("modelRemoveConnection").disabled = locked || (
    (kind === "chat" && activeItems().length <= 1)
    || (kind === "embedding" && state.models.embedding.enabled && activeItems().length <= 1)
  );
  byId("modelTypeSearch").disabled = locked;
  byId("modelInspectorFields").innerHTML = `
    <label class="settings-field full"><span>连接名称</span><input data-model-field="name" value="${escapeHtml(record.name)}" autocomplete="off" required${disabledMarkup(locked)}>${errorMarkup(record.id, "name")}</label>
    <label class="settings-field full"><span>Stable ID</span><input value="${escapeHtml(record.id)}" readonly aria-readonly="true"><small>排序或改名不会改变此 ID。</small>${errorMarkup(record.id, "id")}</label>`;
  const descriptorFields = descriptor ? descriptor.fields : [];
  byId("modelDescriptorFields").innerHTML = descriptorFields
    .map((field) => renderDescriptorField(record, descriptor, field))
    .join("");
  renderConnectionTypes();
  renderCredential(record, descriptor);
  renderProbeStatus(record);
}

function renderProbeStatus(record) {
  const status = byId("modelProbeStatus");
  const probe = record?.probe;
  if (!probe) {
    status.textContent = "尚未探测此精确草稿。";
    delete status.dataset.tone;
    return;
  }
  const dimensions = probe.observed_dimension ? ` · ${probe.observed_dimension} dimensions` : "";
  const latency = probe.latency_ms ? ` · ${probe.latency_ms} ms` : "";
  const timestamp = probe.probed_at ? ` · ${new Date(probe.probed_at).toLocaleString()}` : "";
  status.textContent = `${probe.ok ? "通过" : probe.error_code || "失败"}${dimensions}${latency}${timestamp}`;
  status.dataset.tone = probe.ok ? "success" : "error";
}

function renderRuntime() {
  if (state.activeRoute !== "runtime") return;
  byId("modelChatConcurrency").value = String(state.models.chat.concurrency);
  byId("modelChatConcurrency").disabled = Boolean(
    modelControlLocked("models.chat.concurrency"),
  );
  byId("modelChatTimeout").value = String(state.models.chat.timeout_seconds);
  byId("modelChatTimeout").disabled = Boolean(
    modelControlLocked("models.chat.timeout_seconds"),
  );
  const all = [...state.models.chat.connections, ...state.models.embedding.providers];
  const open = all.filter((record) => record.circuit?.state === "open").length;
  const healthy = all.filter((record) => record.probe?.ok === true).length;
  byId("modelRuntimeSummary").innerHTML = `
    <div class="model-runtime-card"><span>Chat route</span><strong>${state.models.chat.connections.length} connections</strong></div>
    <div class="model-runtime-card"><span>Embedding route</span><strong>${state.models.embedding.providers.length} providers · ${state.models.embedding.enabled ? "enabled" : "disabled"}</strong></div>
    <div class="model-runtime-card"><span>Current health</span><strong>${healthy} passed probes · ${open} open circuits</strong></div>`;
}

function migrationResolution(action) {
  if (action === "add_to_chat_route") {
    return { action };
  }
  if (action === "apply_shared_embedding_settings") {
    return { action, embedding_settings: { ...state.models.embedding.settings } };
  }
  return { action };
}

function renderMigration() {
  const panel = byId("modelMigrationPanel");
  const issues = state.migration?.issues || [];
  panel.hidden = issues.length === 0;
  panel.innerHTML = issues.length ? `
    <div class="model-section-heading"><div><p class="eyebrow">Migration</p><h3>确认旧配置迁移</h3></div></div>
    ${issues.map((issue) => {
      const selected = state.migration_resolutions?.[issue.id]?.action || "";
      return `<article class="model-migration-issue" data-migration-issue="${escapeHtml(issue.id)}">
        <strong>${escapeHtml(issue.reason || issue.code)}</strong>
        <span class="settings-note-inline">${escapeHtml(issue.field)}${issue.provider ? ` · ${escapeHtml(issue.provider)}` : ""}</span>
        <div class="model-migration-actions">${(issue.allowed_actions || []).map((action) => `<button class="small-btn${selected === action ? " is-active" : ""}" type="button" data-migration-action="${escapeHtml(action)}" data-migration-id="${escapeHtml(issue.id)}">${escapeHtml(action.replaceAll("_", " "))}</button>`).join("")}</div>
      </article>`;
    }).join("")}` : "";
}

function render() {
  if (!state) return;
  renderTabs();
  renderRemoteUpdate();
  renderOverrides();
  renderErrorSummary();
  renderMigration();
  renderEmbeddingSettings();
  renderRouteList();
  renderInspector();
  renderRuntime();
  byId("modelSaveButton").disabled = false;
  setStatus(state.dirty ? "有未保存的模型更改。" : `模型配置已同步 · ${state.revision.slice(0, 12)}`);
}

function focusMovedRow(id) {
  window.requestAnimationFrame(() => {
    const row = document.querySelector(`[data-model-record-id="${CSS.escape(id)}"]`);
    if (row) row.focus();
  });
}

function focusNarrowDetail() {
  if (!window.matchMedia("(max-width: 820px)").matches) return;
  window.requestAnimationFrame(() => byId("modelInspectorBack")?.focus());
}

function focusSelectedRouteControl() {
  const record = selectedRecord(state, state.activeRoute);
  if (!record) return;
  window.requestAnimationFrame(() => {
    document.querySelector(`[data-model-select="${CSS.escape(record.id)}"]`)?.focus();
  });
}

function moveSelected(delta) {
  if (routeLocked(state.activeRoute)) return;
  const record = selectedRecord(state, state.activeRoute);
  if (!record) return;
  const target = selectedIndex() + delta;
  state = moveRouteItem(state, state.activeRoute, record.id, target);
  render();
  focusMovedRow(record.id);
}

function selectRecord(id, openDetail = true) {
  state = selectRouteItem(state, state.activeRoute, id);
  byId("modelRouteLayout").classList.toggle("is-detail", openDetail);
  renderRouteList();
  renderInspector();
  if (openDetail) focusNarrowDetail();
}

function addConnection() {
  if (routeLocked(state.activeRoute)) return;
  const descriptor = descriptorsFor(state.activeRoute)[0];
  if (!descriptor) return;
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
    credential: { action: descriptor.category === "oauth" ? "keep" : "clear", value: "" },
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
    byId("modelRouteLayout").classList.add("is-detail");
    render();
    focusNarrowDetail();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function removeSelected() {
  if (routeLocked(state.activeRoute)) return;
  const record = selectedRecord(state, state.activeRoute);
  if (!record || !window.confirm(`移除 ${record.name || record.id}？`)) return;
  try {
    state = removeRouteItem(state, state.activeRoute, record.id);
    byId("modelRouteLayout").classList.remove("is-detail");
    render();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function changeType(typeId) {
  if (routeLocked(state.activeRoute)) return;
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
  const preset = descriptor.preset_definitions?.find((candidate) => candidate.id === updated?.preset);
  if (preset) {
    state = applyPreset(state, state.activeRoute, record.id, preset, { previousPreset });
  }
  render();
}

function updateField(field, target) {
  if (routeLocked(state.activeRoute)) return;
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
        result = changePreset(state, state.activeRoute, record.id, descriptor, preset, {
          confirmed: true,
        });
      }
      state = result.state;
    }
    renderInspector();
  } else {
    state = updateRouteField(state, state.activeRoute, record.id, field, value);
    if (field === "name") {
      byId("modelInspectorTitle").textContent = value || "连接详情";
      renderRouteList();
    }
  }
  setStatus("有未保存的模型更改。");
}

function updateCredential(action, value = "", rerender = true) {
  if (routeLocked(state.activeRoute)) return;
  const record = selectedRecord(state, state.activeRoute);
  if (!record) return;
  state = updateRouteField(state, state.activeRoute, record.id, "credential", { action, value });
  if (rerender) {
    renderCredential(selectedRecord(state, state.activeRoute), descriptorFor(record.type));
  }
  setStatus("有未保存的模型更改。");
}

async function probeSelected() {
  const record = selectedRecord(state, state.activeRoute);
  if (!record || state.activeRoute === "runtime") return;
  const button = byId("modelProbeButton");
  const status = byId("modelProbeStatus");
  const payload = toModelConfigPayload(state);
  const selectedDraft = state.activeRoute === "chat"
    ? payload.models.chat.connections.find((item) => item.id === record.id)
    : payload.models.embedding.providers.find((item) => item.id === record.id);
  const body = state.activeRoute === "chat"
    ? {
      kind: state.activeRoute,
      revision: state.revision,
      connection: selectedDraft,
    }
    : {
      kind: state.activeRoute,
      revision: state.revision,
      provider: selectedDraft,
      settings: payload.models.embedding.settings,
    };
  button.disabled = true;
  status.textContent = "正在探测精确草稿…";
  const started = performance.now();
  try {
    const result = await requestModelJson(MODEL_PROBE_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    record.probe = { ...result, latency_ms: Math.round(performance.now() - started) };
    renderProbeStatus(record);
    renderRouteList();
  } catch (error) {
    if (error.status === 409 && error.details?.latest) {
      state = receiveRemoteSnapshot(state, error.details.latest);
      render();
    }
    status.textContent = error.details?.error || error.message || "Probe failed";
    status.dataset.tone = "error";
  } finally {
    button.disabled = false;
  }
}

function retainSelection(next, previous) {
  for (const kind of ["chat", "embedding"]) {
    const id = previous?.selected?.[kind];
    const items = kind === "chat" ? next.models.chat.connections : next.models.embedding.providers;
    if (id && items.some((item) => item.id === id)) next.selected[kind] = id;
  }
  next.activeRoute = previous?.activeRoute || "chat";
  return next;
}

async function saveModels() {
  if (!state) return;
  const button = byId("modelSaveButton");
  button.disabled = true;
  setStatus("正在验证并热重载模型 route…");
  try {
    const result = await requestModelJson(MODEL_CONFIG_API, {
      method: "PUT",
      timeoutMs: 60000,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(toModelConfigPayload(state)),
    });
    state = retainSelection(hydrateModelConfig(result.snapshot), state);
    render();
    setStatus("模型 route 已保存并热重载。", "success");
    showToast("模型 route 已保存");
  } catch (error) {
    if (error.status === 409 && error.details?.error === "revision_conflict") {
      state = receiveRemoteSnapshot(state, error.details.latest);
      render();
      setStatus("保存被拒绝：远端已有更新。", "error");
    } else if (Array.isArray(error.details?.errors)) {
      state = mapServerFieldErrors(state, error.details.errors);
      render();
      setStatus("请修正标记的模型字段。", "error");
    } else {
      setStatus(error.details?.error || error.message || "模型保存失败。", "error");
    }
  } finally {
    button.disabled = false;
  }
}

async function fetchModelSnapshot(remote = false) {
  const snapshot = await requestModelJson(MODEL_CONFIG_API);
  if (remote && state) state = receiveRemoteSnapshot(state, snapshot);
  else state = retainSelection(hydrateModelConfig(snapshot), state);
  render();
}

async function loadModelSettings() {
  setStatus("正在读取模型配置…");
  try {
    const [snapshot, descriptors] = await Promise.all([
      requestModelJson(MODEL_CONFIG_API),
      requestModelJson(CONNECTION_TYPES_API),
    ]);
    connectionTypes = descriptors;
    state = hydrateModelConfig(snapshot);
    state.activeRoute = "chat";
    render();
  } catch (error) {
    setStatus(error.message || "无法读取模型配置。", "error");
  }
}

function confirmLeave() {
  return !state?.dirty || window.confirm("模型 route 有未保存的更改，确定离开吗？");
}

function bindEvents() {
  document.querySelectorAll("[data-model-route]").forEach((tab) => {
    tab.addEventListener("click", () => {
      if (!state) return;
      state.activeRoute = tab.dataset.modelRoute;
      byId("modelRouteLayout").classList.remove("is-detail");
      render();
    });
  });
  byId("modelAddConnection").addEventListener("click", addConnection);
  byId("modelRemoveConnection").addEventListener("click", removeSelected);
  byId("modelMoveUp").addEventListener("click", () => moveSelected(-1));
  byId("modelMoveDown").addEventListener("click", () => moveSelected(1));
  byId("modelInspectorBack").addEventListener("click", () => {
    byId("modelRouteLayout").classList.remove("is-detail");
    focusSelectedRouteControl();
  });
  byId("modelSaveButton").addEventListener("click", () => void saveModels());
  byId("modelProbeButton").addEventListener("click", () => void probeSelected());
  byId("modelReloadRemote").addEventListener("click", () => {
    if (!state?.remoteUpdate || !window.confirm("放弃当前草稿并加载远端模型配置？")) return;
    state = retainSelection(hydrateModelConfig(state.remoteUpdate.snapshot), state);
    render();
  });
  byId("modelTypeSearch").addEventListener("input", renderConnectionTypes);
  byId("modelConnectionTypeGroups").addEventListener("click", (event) => {
    const button = event.target.closest("[data-model-type]");
    if (button) changeType(button.dataset.modelType);
  });
  byId("modelConnectionTypeGroups").addEventListener("keydown", moveTypeOptionFocus);
  byId("modelInspectorFields").addEventListener("input", (event) => {
    const field = event.target.dataset.modelField;
    if (field) updateField(field, event.target);
  });
  byId("modelDescriptorFields").addEventListener("input", (event) => {
    const field = event.target.dataset.modelField;
    if (field && event.target.tagName !== "SELECT") updateField(field, event.target);
  });
  byId("modelDescriptorFields").addEventListener("change", (event) => {
    const field = event.target.dataset.modelField;
    if (field) updateField(field, event.target);
  });
  byId("modelCredentialEditor").addEventListener("click", (event) => {
    const action = event.target.closest("[data-model-credential-action]")?.dataset.modelCredentialAction;
    if (action) updateCredential(action);
  });
  byId("modelCredentialEditor").addEventListener("input", (event) => {
    if (event.target.id === "modelCredentialValue") {
      const record = selectedRecord(state, state.activeRoute);
      updateCredential(record.credential.action, event.target.value, false);
    }
  });
  byId("modelRouteList").addEventListener("click", (event) => {
    const id = event.target.closest("[data-model-select]")?.dataset.modelSelect;
    if (id) selectRecord(id);
  });
  byId("modelRouteList").addEventListener("keydown", (event) => {
    const row = event.target.closest("[data-model-record-id]");
    if (!row || !event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
    if (routeLocked(state.activeRoute)) return;
    event.preventDefault();
    selectRecord(row.dataset.modelRecordId, false);
    moveSelected(event.key === "ArrowUp" ? -1 : 1);
  });
  byId("modelRouteList").addEventListener("dragstart", (event) => {
    const row = event.target.closest("[data-model-record-id]");
    if (!row || routeLocked(state.activeRoute)) {
      event.preventDefault();
      return;
    }
    draggedId = row.dataset.modelRecordId;
    row.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
  });
  byId("modelRouteList").addEventListener("dragover", (event) => event.preventDefault());
  byId("modelRouteList").addEventListener("drop", (event) => {
    event.preventDefault();
    if (routeLocked(state.activeRoute)) return;
    const target = event.target.closest("[data-model-record-id]");
    if (!target || !draggedId) return;
    const targetIndex = activeItems().findIndex((item) => item.id === target.dataset.modelRecordId);
    state = moveRouteItem(state, state.activeRoute, draggedId, targetIndex);
    const moved = draggedId;
    draggedId = "";
    render();
    focusMovedRow(moved);
  });
  byId("modelRouteList").addEventListener("dragend", () => {
    draggedId = "";
    document.querySelectorAll(".model-route-row.is-dragging").forEach((row) => row.classList.remove("is-dragging"));
  });
  byId("modelEmbeddingEnabled").addEventListener("change", (event) => {
    if (modelControlLocked("models.embedding.enabled")) {
      renderEmbeddingSettings();
      return;
    }
    const providersLocked = Boolean(routeLocked("embedding"));
    if (!event.target.checked && state.models.embedding.providers.length && !providersLocked) {
      if (!window.confirm("停用 Embedding 会清空当前 Provider route。继续吗？")) {
        event.target.checked = true;
        return;
      }
      state = updateRouteSetting(state, "embedding", "enabled", false);
      state.models.embedding.providers = [];
      state.selected.embedding = "";
    } else {
      state = updateRouteSetting(state, "embedding", "enabled", event.target.checked);
      if (
        event.target.checked
        && state.models.embedding.providers.length === 0
        && !providersLocked
      ) addConnection();
    }
    render();
  });
  for (const [id, field, kind, path] of [
    ["modelEmbeddingModel", "model", "text", "models.embedding.settings.model"],
    ["modelEmbeddingDimension", "output_dimensionality", "number", "models.embedding.settings.output_dimensionality"],
    ["modelEmbeddingSimilarity", "similarity_threshold", "number", "models.embedding.settings.similarity_threshold"],
  ]) {
    byId(id).addEventListener("input", (event) => {
      if (modelControlLocked(path)) return;
      const value = kind === "number" ? Number(event.target.value) : event.target.value;
      state = updateRouteSetting(state, "embedding", field, value);
      setStatus("有未保存的模型更改。");
    });
  }
  byId("modelEmbeddingMultimodal").addEventListener("change", (event) => {
    if (modelControlLocked("models.embedding.settings.multimodal_enabled")) return;
    state = updateRouteSetting(state, "embedding", "multimodal_enabled", event.target.checked);
    setStatus("有未保存的模型更改。");
  });
  byId("modelChatConcurrency").addEventListener("input", (event) => {
    if (modelControlLocked("models.chat.concurrency")) return;
    state = updateRouteSetting(state, "chat", "concurrency", Number(event.target.value));
    setStatus("有未保存的模型更改。");
  });
  byId("modelChatTimeout").addEventListener("input", (event) => {
    if (modelControlLocked("models.chat.timeout_seconds")) return;
    state = updateRouteSetting(state, "chat", "timeout_seconds", Number(event.target.value));
    setStatus("有未保存的模型更改。");
  });
  byId("modelMigrationPanel").addEventListener("click", (event) => {
    const button = event.target.closest("[data-migration-action]");
    if (!button) return;
    state = setMigrationResolution(
      state,
      button.dataset.migrationId,
      migrationResolution(button.dataset.migrationAction),
    );
    renderMigration();
    setStatus("迁移选择尚未保存。");
  });
  window.addEventListener("beforeunload", (event) => {
    if (!state?.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("openbiliclaw:config-reloaded", (event) => {
    if (event.detail?.type && event.detail.type !== CONFIG_RELOADED_TYPE) return;
    void fetchModelSnapshot(true).catch(() => {});
  });
}

function init() {
  if (!byId("modelRouteTabs")) return;
  bindEvents();
  window.OpenBiliClawModelSettings = {
    isDirty: () => Boolean(state?.dirty),
    confirmLeave,
    reload: () => fetchModelSnapshot(false),
  };
  void loadModelSettings();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
else init();
