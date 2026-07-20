import {
  appendRouteItem,
  applyLatestSnapshotRequest,
  applyProbeResult,
  applyPreset,
  changeConnectionType,
  changePreset,
  createLatestRequestGate,
  createModelOperationGate,
  createProbeSignature,
  hydrateModelConfig,
  loadIndependentModelResources,
  mapServerFieldErrors,
  moveRouteItem,
  prepareLocalOllamaEmbedding,
  probeSignatureMatches,
  receiveRemoteSnapshot,
  removeRouteItem,
  selectRouteItem,
  selectedRecord,
  setMigrationResolution,
  toModelConfigPayload,
  updateRouteField,
  updateRouteSetting,
} from "./popup-model-config-state.js";
import {
  fetchModelConfig,
  fetchModelConnectionTypes,
  probeModelConnection,
  updateModelConfig,
} from "./popup-api.js";
import type {
  ConnectionDescriptor,
  DescriptorField,
  EmbeddingSettings,
  ModelConfigState,
  PresetDefinition,
  ProbeSignature,
  RawSnapshot,
  RouteKind,
  RouteRecord,
} from "./popup-model-config-state.js";

type EditorRoute = RouteKind | "runtime";

interface PopupPreset extends PresetDefinition {
  label: string;
}

interface PopupDescriptorField extends DescriptorField {
  label: string;
  help?: string;
  input_type?: string;
  required?: boolean;
  choices?: string[];
  placeholder?: string;
}

interface PopupConnectionDescriptor extends ConnectionDescriptor {
  id: string;
  label: string;
  category: string;
  help?: string;
  fields: PopupDescriptorField[];
  preset_definitions?: PopupPreset[];
}

interface ConnectionTypeGroup {
  category: string;
  connection_types: PopupConnectionDescriptor[];
}

interface ConnectionTypesPayload {
  connection_types: PopupConnectionDescriptor[];
  groups: ConnectionTypeGroup[];
}

interface MigrationIssue {
  id: string;
  reason?: string;
  code: string;
  field: string;
  provider?: string;
  allowed_actions?: string[];
}

type EditorState = ModelConfigState & {
  activeRoute: EditorRoute;
  migration: { issues?: MigrationIssue[] } | null;
  migration_resolutions: Record<string, { action?: string }>;
  remoteUpdate: { latestRevision: string; snapshot: RawSnapshot } | null;
};

interface ModelConfigPayload {
  models: {
    chat: { connections: RouteRecord[] };
    embedding: { providers: RouteRecord[]; settings: EmbeddingSettings };
  };
}

interface ModelConfigUpdateResponse {
  snapshot: RawSnapshot;
  [key: string]: unknown;
}

interface ModelErrorDetails {
  error?: string;
  latest?: RawSnapshot;
  errors?: unknown[];
}

interface ModelApiError extends Error {
  status?: number;
  details?: ModelErrorDetails;
}

interface InitModelSettingsOptions {
  onToast?: (message: string, tone: string) => void;
}

const CONFIG_RELOADED_TYPE = "config_reloaded";
const CATEGORY_LABELS: Record<string, string> = {
  api_protocol: "API 协议",
  local_runtime: "本地 Runtime",
  oauth: "OAuth 连接",
};
const ROUTE_OVERRIDE_PATHS: Record<RouteKind, string> = {
  chat: "models.chat.connections",
  embedding: "models.embedding.providers",
};

let state = null as unknown as EditorState;
let connectionTypes: ConnectionTypesPayload = { connection_types: [], groups: [] };
let draggedId = "";
let initialized = false;
let notify: (message: string, tone: string) => void = () => {};
const modelOperations = createModelOperationGate();
const snapshotRequestGate = createLatestRequestGate();
const descriptorRequestGate = createLatestRequestGate();

const byId = <T extends HTMLElement = HTMLInputElement>(id: string) => (
  document.getElementById(id) as T
);
const disabledMarkup = (disabled: boolean) => (disabled ? ' disabled aria-disabled="true"' : "");
const escapeHtml = (value: unknown) => String(value ?? "").replace(
  /[&<>'"]/g,
  (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  } as Record<string, string>)[character],
);

function modelControlLocked(path: string) {
  if (!state) return null;
  return state.overrideLocks?.[path] || null;
}

function routeLocked(kind: EditorRoute) {
  return kind === "chat" || kind === "embedding"
    ? modelControlLocked(ROUTE_OVERRIDE_PATHS[kind])
    : null;
}

function modelMutationBlocked() {
  return !state || modelOperations.saveInFlight;
}

function setModelEditorLocked(locked: boolean) {
  const controls = modelOperations.controlState();
  const editorLocked = Boolean(locked || controls.editorLocked);
  const boundary = byId("popupModelEditorBoundary");
  if (!boundary) return;
  boundary.disabled = editorLocked;
  boundary.inert = editorLocked;
  boundary.setAttribute("aria-busy", editorLocked ? "true" : "false");
  byId("popupModelSaveButton").disabled = editorLocked || controls.saveDisabled;
  byId("popupModelProbeButton").disabled = editorLocked || controls.probeDisabled;
}

function probeRequestVisible(signature: ProbeSignature) {
  return Boolean(
    state
    && state.activeRoute === signature.kind
    && state.selected?.[signature.kind] === signature.id,
  );
}

function activeItems() {
  if (!state || state.activeRoute === "runtime") return [];
  return state.activeRoute === "chat"
    ? state.models.chat.connections
    : state.models.embedding.providers;
}

function descriptorFor(typeId: string) {
  return connectionTypes.connection_types.find((descriptor) => descriptor.id === typeId) || null;
}

function presetFor(descriptor: PopupConnectionDescriptor | null, presetId: string) {
  return descriptor?.preset_definitions?.find((preset) => preset.id === presetId) || null;
}

function descriptorsFor(kind: RouteKind) {
  return connectionTypes.connection_types.filter(
    (descriptor) => descriptor.capabilities?.includes(kind),
  );
}

function selectedIndex() {
  if (state.activeRoute === "runtime") return -1;
  const record = selectedRecord(state, state.activeRoute);
  return record ? activeItems().findIndex((item) => item.id === record.id) : -1;
}

function derivedRole(index: number) {
  return index === 0 ? "Primary" : `Fallback ${index}`;
}

function fieldError(recordId: string, field: string) {
  return state?.fieldErrors?.byConnection?.[recordId]?.[field] || null;
}

function errorMarkup(recordId: string, field: string) {
  const error = fieldError(recordId, field);
  return error ? `<span class="model-field-error" role="alert">${escapeHtml(error.message)}</span>` : "";
}

function uniqueId(kind: RouteKind) {
  const token = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${kind}-${token}`;
}

function setStatus(message: string, tone = "") {
  const element = byId("popupModelSaveStatus");
  if (!element) return;
  element.textContent = message;
  if (tone) element.dataset.tone = tone;
  else delete element.dataset.tone;
}

function safeHealth(record: RouteRecord) {
  if (record?.circuit?.state === "open") {
    return { label: record.circuit.failure_kind || "熔断已打开", tone: "error" };
  }
  if (record?.probe?.ok === true) return { label: "探测通过", tone: "success" };
  if (record?.probe?.ok === false) return { label: record.probe.error_code || "探测失败", tone: "error" };
  return { label: "尚未探测", tone: "" };
}

function renderTabs() {
  document.querySelectorAll<HTMLElement>("[data-popup-model-route]").forEach((tab) => {
    const active = tab.dataset.popupModelRoute === state.activeRoute;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  const runtime = state.activeRoute === "runtime";
  byId("popupModelRuntimeView").hidden = !runtime;
  document.querySelector<HTMLElement>('[data-popup-model-view="route"]')!.hidden = runtime;
  byId("popupModelEmbeddingSharedSettings").hidden = state.activeRoute !== "embedding";
}

function renderRemoteUpdate() {
  byId("popupModelRemoteBanner").hidden = !state.remoteUpdate;
}

function renderOverrides() {
  const host = byId("popupModelOverrideNotice");
  const overrides = state.overrides || [];
  host.hidden = overrides.length === 0;
  host.innerHTML = overrides.length ? `
    <strong>只读模型覆盖</strong>
    <p>以下字段由高优先级配置提供；对应编辑器已锁定，其余基础配置仍可保存。</p>
    <ul>${overrides.map((override) => `
      <li><code>${escapeHtml(override.path)}</code><span>${escapeHtml(override.source)}</span></li>`).join("")}</ul>` : "";
}

function renderErrorSummary() {
  const summary = byId("popupModelErrorSummary");
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
  byId("popupModelEmbeddingEnabled").checked = state.models.embedding.enabled;
  byId("popupModelEmbeddingEnabled").disabled = Boolean(
    modelControlLocked("models.embedding.enabled"),
  );
  byId("popupModelEmbeddingModel").value = state.models.embedding.settings.model;
  byId("popupModelEmbeddingModel").disabled = Boolean(
    modelControlLocked("models.embedding.settings.model"),
  );
  byId("popupModelEmbeddingDimension").value = String(
    state.models.embedding.settings.output_dimensionality,
  );
  byId("popupModelEmbeddingDimension").disabled = Boolean(
    modelControlLocked("models.embedding.settings.output_dimensionality"),
  );
  byId("popupModelEmbeddingSimilarity").value = String(
    state.models.embedding.settings.similarity_threshold,
  );
  byId("popupModelEmbeddingSimilarity").disabled = Boolean(
    modelControlLocked("models.embedding.settings.similarity_threshold"),
  );
  byId("popupModelEmbeddingMultimodal").checked = state.models.embedding.settings.multimodal_enabled;
  byId("popupModelEmbeddingMultimodal").disabled = Boolean(
    modelControlLocked("models.embedding.settings.multimodal_enabled"),
  );
}

function renderRouteList() {
  if (state.activeRoute === "runtime") return;
  const kind = state.activeRoute;
  const items = activeItems();
  const locked = routeLocked(kind);
  byId("popupModelRouteTitle").textContent = kind === "chat" ? "Chat 连接" : "Embedding Provider";
  byId("popupModelRouteHelp").textContent = kind === "chat"
    ? "第 1 项是 Primary，其余项按顺序作为 Fallback；最多 10 项。"
    : "所有 Provider 按此顺序 Fallback，并共享上方唯一模型设置；最多 10 项。";
  byId("popupModelAddConnection").disabled = Boolean(locked) || items.length >= 10 || (
    kind === "embedding" && !state.models.embedding.enabled
  );
  byId("popupModelRouteList").innerHTML = items.map((record, index) => {
    const descriptor = descriptorFor(record.type);
    const preset = presetFor(descriptor, record.preset);
    const health = safeHealth(record);
    const model = kind === "chat" ? record.model : state.models.embedding.settings.model;
    const selected = state.selected[kind] === record.id;
    return `
      <div class="model-route-row${selected ? " is-selected" : ""}" draggable="${locked ? "false" : "true"}" data-model-record-id="${escapeHtml(record.id)}" tabindex="${selected ? "0" : "-1"}" aria-current="${selected ? "true" : "false"}">
        <span class="model-route-drag-handle" aria-label="拖拽排序" title="${locked ? "顺序由只读覆盖配置提供" : "拖拽排序"}" aria-disabled="${locked ? "true" : "false"}">⋮⋮</span>
        <button class="model-route-row-copy" type="button" data-model-select="${escapeHtml(record.id)}">
          <strong>${escapeHtml(derivedRole(index))} · ${escapeHtml(record.name || "未命名连接")}</strong>
          <span>${escapeHtml(descriptor?.label || record.type)}${preset ? ` / ${escapeHtml(preset.label)}` : ""} · ${escapeHtml(model || "未设置模型")}</span>
        </button>
        <span class="model-route-health" data-tone="${health.tone}">${escapeHtml(health.label)}</span>
      </div>`;
  }).join("") || '<p class="settings-note-inline">当前路由为空。</p>';
}

function renderConnectionTypes() {
  const record = selectedRecord(state, state.activeRoute as RouteKind);
  if (!record) return;
  const locked = Boolean(routeLocked(state.activeRoute));
  const query = String(byId("popupModelTypeSearch")?.value || "").trim().toLowerCase();
  const host = byId("popupModelConnectionTypeGroups");
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
  const options = [...(host.querySelectorAll<HTMLElement>(
    '[role="option"]:not(:disabled)',
  ) as unknown as Iterable<HTMLElement>)];
  const selected = options.find((option) => option.getAttribute("aria-selected") === "true");
  const roving = selected || options[0];
  if (roving) roving.tabIndex = 0;
}

function moveTypeOptionFocus(event: KeyboardEvent) {
  if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
  const options = [...((event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>(
    '[role="option"]:not(:disabled)',
  ) as unknown as Iterable<HTMLElement>)];
  if (!options.length) return;
  const current = (event.target as HTMLElement).closest<HTMLElement>('[role="option"]');
  let index = Math.max(0, current ? options.indexOf(current) : -1);
  if (event.key === "Home") index = 0;
  else if (event.key === "End") index = options.length - 1;
  else if (event.key === "ArrowUp") index = Math.max(0, index - 1);
  else index = Math.min(options.length - 1, index + 1);
  event.preventDefault();
  for (const option of options) option.tabIndex = option === options[index] ? 0 : -1;
  options[index].focus();
}

function focusSelectedTypeOption() {
  const record = selectedRecord(state, state.activeRoute as RouteKind);
  if (!record) return;
  window.requestAnimationFrame(() => {
    byId("popupModelConnectionTypeGroups")
      ?.querySelector<HTMLElement>(`[data-model-type="${CSS.escape(record.type)}"]`)
      ?.focus();
  });
}

function renderDescriptorField(
  record: RouteRecord,
  descriptor: PopupConnectionDescriptor,
  field: PopupDescriptorField,
) {
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
      <select data-model-field="${escapeHtml(field.name)}"${required}${disabled}>${(field.choices || []).map((choice) => `<option value="${escapeHtml(choice)}"${String(record[field.name] || "") === choice ? " selected" : ""}>${escapeHtml(field.name === "reasoning_effort" && choice === "" ? "disabled" : choice)}</option>`).join("")}</select>
      ${help}${errorMarkup(record.id, field.name)}</label>`;
  }
  const type = field.input_type === "number" ? "number" : "text";
  return `<label class="settings-field"><span>${escapeHtml(field.label)}</span>
    <input type="${type}" data-model-field="${escapeHtml(field.name)}" value="${escapeHtml(record[field.name] ?? "")}" placeholder="${escapeHtml(field.placeholder || "")}" autocomplete="off"${required}${disabled}>
    ${help}${errorMarkup(record.id, field.name)}</label>`;
}

function renderCredential(
  record: RouteRecord,
  descriptor: PopupConnectionDescriptor | null,
) {
  const host = byId("popupModelCredentialEditor");
  const definition = descriptor?.fields?.find((field) => field.name === "credential");
  if (!descriptor || !definition) {
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
      <strong>已导入 OAuth 凭据</strong>
      <p class="settings-note-inline">${status.oauth_logged_in ? "已登录" : "尚未检测到登录"} · ${escapeHtml(importedReference)}</p>
      <input type="hidden" data-model-credential-action="keep" value="keep">
      ${errorMarkup(record.id, "credential")}`;
    return;
  }
  const actions = [
    ["keep", "保留现有凭据"],
    ["set", "设置 API Key"],
    ["env", "环境变量"],
    ["clear", "清除"],
  ];
  const sourceLabel = status.configured
    ? `当前来源：${status.source}${status.env_name ? ` (${status.env_name})` : ""}`
    : "当前未配置凭据。";
  const needsValue = credential.action === "set" || credential.action === "env";
  host.innerHTML = `
    <strong>凭据来源</strong>
    <p class="settings-note-inline">${escapeHtml(sourceLabel)}</p>
    <div class="model-credential-actions">${actions.map(([action, label]) => `<button class="model-credential-action" type="button" data-model-credential-action="${action}" aria-pressed="${credential.action === action ? "true" : "false"}"${disabled}>${label}</button>`).join("")}</div>
    ${needsValue ? `<label class="settings-field"><span>${credential.action === "env" ? "环境变量名" : "新 API Key"}</span><input id="popupModelCredentialValue" type="${credential.action === "set" ? "password" : "text"}" value="${escapeHtml(credential.value || "")}" autocomplete="new-password"${disabled}></label>` : ""}
    ${errorMarkup(record.id, "credential")}`;
}

function renderInspector() {
  if (state.activeRoute === "runtime") return;
  const kind = state.activeRoute;
  const record = selectedRecord(state, kind);
  const inspector = byId("popupModelDetail");
  inspector.hidden = !record;
  if (!record) return;
  const index = selectedIndex();
  const descriptor = descriptorFor(record.type);
  const locked = Boolean(routeLocked(kind));
  byId("popupModelInspectorRole").textContent = derivedRole(index);
  byId("popupModelInspectorTitle").textContent = record.name || "连接详情";
  byId("popupModelMoveUp").disabled = locked || index <= 0;
  byId("popupModelMoveDown").disabled = locked || index < 0 || index >= activeItems().length - 1;
  byId("popupModelRemoveConnection").disabled = locked || (
    (kind === "chat" && activeItems().length <= 1)
    || (kind === "embedding" && state.models.embedding.enabled && activeItems().length <= 1)
  );
  byId("popupModelTypeSearch").disabled = locked;
  byId("popupModelInspectorFields").innerHTML = `
    <label class="settings-field full"><span>连接名称</span><input data-model-field="name" value="${escapeHtml(record.name)}" autocomplete="off" required${disabledMarkup(locked)}>${errorMarkup(record.id, "name")}</label>
    <label class="settings-field full"><span>稳定 ID</span><input value="${escapeHtml(record.id)}" readonly aria-readonly="true"><small>排序或改名不会改变此 ID。</small>${errorMarkup(record.id, "id")}</label>`;
  byId("popupModelDescriptorFields").innerHTML = descriptor
    ? descriptor.fields.map((field) => renderDescriptorField(record, descriptor, field)).join("")
    : "";
  renderConnectionTypes();
  renderCredential(record, descriptor);
  renderProbeStatus(record);
}

function renderProbeStatus(record: RouteRecord | null) {
  const status = byId("popupModelProbeStatus");
  const probe = record?.probe;
  if (!probe) {
    status.textContent = "尚未探测此精确草稿。";
    delete status.dataset.tone;
    return;
  }
  const dimensions = probe.observed_dimension ? ` · ${probe.observed_dimension} 维` : "";
  const latency = probe.latency_ms ? ` · ${probe.latency_ms} ms` : "";
  const timestamp = probe.probed_at
    ? ` · ${new Date(String(probe.probed_at)).toLocaleString()}`
    : "";
  status.textContent = `${probe.ok ? "通过" : probe.error_code || "失败"}${dimensions}${latency}${timestamp}`;
  status.dataset.tone = probe.ok ? "success" : "error";
}

function beginModelSave() {
  const save = modelOperations.beginSave();
  if (save?.invalidatedProbe && state) {
    renderProbeStatus(selectedRecord(state, state.activeRoute as RouteKind));
  }
  return save;
}

function renderRuntime() {
  if (state.activeRoute !== "runtime") return;
  byId("popupModelChatConcurrency").value = String(state.models.chat.concurrency);
  byId("popupModelChatConcurrency").disabled = Boolean(
    modelControlLocked("models.chat.concurrency"),
  );
  byId("popupModelChatTimeout").value = String(state.models.chat.timeout_seconds);
  byId("popupModelChatTimeout").disabled = Boolean(
    modelControlLocked("models.chat.timeout_seconds"),
  );
  const all = [...state.models.chat.connections, ...state.models.embedding.providers];
  const open = all.filter((record) => record.circuit?.state === "open").length;
  const healthy = all.filter((record) => record.probe?.ok === true).length;
  byId("popupModelRuntimeSummary").innerHTML = `
    <div class="model-runtime-card"><span>Chat 路由</span><strong>${state.models.chat.connections.length} 个连接</strong></div>
    <div class="model-runtime-card"><span>Embedding 路由</span><strong>${state.models.embedding.providers.length} 个 Provider · ${state.models.embedding.enabled ? "已启用" : "已停用"}</strong></div>
    <div class="model-runtime-card"><span>当前健康状态</span><strong>${healthy} 个探测通过 · ${open} 个熔断打开</strong></div>`;
}

function migrationResolution(action: string): Record<string, unknown> {
  if (action === "add_to_chat_route") {
    return { action };
  }
  if (action === "apply_shared_embedding_settings") {
    return { action, embedding_settings: { ...state.models.embedding.settings } };
  }
  return { action };
}

function renderMigration() {
  const panel = byId("popupModelMigrationPanel");
  const issues = state.migration?.issues || [];
  panel.hidden = issues.length === 0;
  panel.innerHTML = issues.length ? `
    <div class="model-section-heading"><div><p class="eyebrow">迁移</p><h3>确认旧配置迁移</h3></div></div>
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
  const controls = modelOperations.controlState();
  byId("popupModelSaveButton").disabled = controls.saveDisabled;
  byId("popupModelProbeButton").disabled = controls.probeDisabled;
  setStatus(state.dirty ? "有未保存的模型更改。" : `模型配置已同步 · ${state.revision.slice(0, 12)}`);
}

function focusMovedRow(id: string) {
  window.requestAnimationFrame(() => {
    const row = document.querySelector<HTMLElement>(`[data-model-record-id="${CSS.escape(id)}"]`);
    if (row) row.focus();
  });
}

function focusNarrowDetail() {
  window.requestAnimationFrame(() => byId("popupModelDetailBack")?.focus());
}

function focusSelectedRouteControl() {
  const kind = state.activeRoute;
  if (kind === "runtime") return;
  const record = selectedRecord(state, kind);
  if (!record) return;
  window.requestAnimationFrame(() => {
    document.querySelector<HTMLElement>(`[data-model-select="${CSS.escape(record.id)}"]`)?.focus();
  });
}

function moveSelected(delta: number) {
  if (modelMutationBlocked()) return;
  const kind = state.activeRoute;
  if (kind === "runtime" || routeLocked(kind)) return;
  const record = selectedRecord(state, kind);
  if (!record) return;
  const target = selectedIndex() + delta;
  state = moveRouteItem(state, kind, record.id, target);
  render();
  focusMovedRow(record.id);
}

function selectRecord(id: string, openDetail = true) {
  if (modelMutationBlocked()) return;
  const kind = state.activeRoute;
  if (kind === "runtime") return;
  state = selectRouteItem(state, kind, id);
  byId("popupModelRouteLayout").classList.toggle("is-detail", openDetail);
  renderRouteList();
  renderInspector();
  if (openDetail) focusNarrowDetail();
}

function addConnection() {
  if (modelMutationBlocked()) return;
  const kind = state.activeRoute;
  if (kind === "runtime" || routeLocked(kind)) return;
  const descriptor = descriptorsFor(kind)[0];
  if (!descriptor) return;
  const preset = descriptor.preset_definitions?.find(
    (candidate) => candidate.capabilities?.includes(kind),
  );
  const id = uniqueId(kind);
  const record = {
    id,
    name: descriptor.label,
    type: descriptor.id,
    preset: preset?.id || "",
    base_url: "",
    credential: { action: descriptor.category === "oauth" ? "keep" : "clear", value: "" },
    ...(kind === "chat" ? {
      model: "",
      api_mode: "",
      reasoning_effort: "",
      http_referer: "",
      x_title: "",
      num_ctx: 0,
    } : {}),
  };
  try {
    state = appendRouteItem(state, kind, record);
    if (preset) state = applyPreset(state, kind, id, preset);
    byId("popupModelRouteLayout").classList.add("is-detail");
    render();
    focusNarrowDetail();
  } catch (error) {
    setStatus((error as Error).message, "error");
  }
}

function removeSelected() {
  if (modelMutationBlocked()) return;
  const kind = state.activeRoute;
  if (kind === "runtime" || routeLocked(kind)) return;
  const record = selectedRecord(state, kind);
  if (!record || !window.confirm(`移除 ${record.name || record.id}？`)) return;
  try {
    state = removeRouteItem(state, kind, record.id);
    byId("popupModelRouteLayout").classList.remove("is-detail");
    render();
  } catch (error) {
    setStatus((error as Error).message, "error");
  }
}

function changeType(typeId: string) {
  if (modelMutationBlocked()) return;
  const kind = state.activeRoute;
  if (kind === "runtime" || routeLocked(kind)) return;
  const record = selectedRecord(state, kind);
  const descriptor = descriptorFor(typeId);
  if (!record || !descriptor || record.type === typeId) return;
  const previousDescriptor = descriptorFor(record.type);
  const previousPreset = presetFor(previousDescriptor, record.preset);
  let result = changeConnectionType(state, kind, record.id, descriptor, {
    confirmed: false,
    previousDescriptor,
  });
  if (result.incompatibleFields.length) {
    const confirmed = window.confirm(
      `切换连接类型会清除这些不兼容字段：${result.incompatibleFields.join(", ")}。继续吗？`,
    );
    if (!confirmed) return;
    result = changeConnectionType(state, kind, record.id, descriptor, {
      confirmed: true,
      previousDescriptor,
    });
  }
  state = result.state;
  const updated = selectedRecord(state, kind);
  const preset = descriptor.preset_definitions?.find((candidate) => candidate.id === updated?.preset);
  if (preset) {
    state = applyPreset(state, kind, record.id, preset, { previousPreset });
  }
  render();
  focusSelectedTypeOption();
}

function updateField(field: string, target: HTMLInputElement | HTMLSelectElement) {
  if (modelMutationBlocked()) return;
  const kind = state.activeRoute;
  if (kind === "runtime" || routeLocked(kind)) return;
  const record = selectedRecord(state, kind);
  if (!record) return;
  let value: string | number = target.value;
  if (target.type === "number") value = Number(target.value);
  if (field === "preset") {
    const descriptor = descriptorFor(record.type);
    const preset = presetFor(descriptor, String(value));
    if (preset) {
      let result = changePreset(state, kind, record.id, descriptor, preset, {
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
        result = changePreset(state, kind, record.id, descriptor, preset, {
          confirmed: true,
        });
      }
      state = result.state;
    }
    renderInspector();
  } else {
    state = updateRouteField(state, kind, record.id, field, value);
    if (field === "name") {
      byId("popupModelInspectorTitle").textContent = String(value || "连接详情");
      renderRouteList();
    }
  }
  setStatus("有未保存的模型更改。");
}

function updateCredential(action: string, value = "", rerender = true) {
  if (modelMutationBlocked()) return;
  const kind = state.activeRoute;
  if (kind === "runtime" || routeLocked(kind)) return;
  const record = selectedRecord(state, kind);
  if (!record) return;
  state = updateRouteField(state, kind, record.id, "credential", { action, value });
  if (rerender) {
    const updated = selectedRecord(state, kind);
    if (updated) renderCredential(updated, descriptorFor(record.type));
  }
  setStatus("有未保存的模型更改。");
}

async function probeSelected() {
  if (!state || modelOperations.saveInFlight) return;
  const kind = state.activeRoute;
  if (kind === "runtime") return;
  const record = selectedRecord(state, kind);
  if (!record) return;
  const generation = modelOperations.beginProbe();
  const signature = createProbeSignature(state, kind, record.id);
  const status = byId("popupModelProbeStatus");
  const payload = toModelConfigPayload(state);
  const selectedDraft = kind === "chat"
    ? payload.models.chat.connections.find((item) => item.id === record.id)
    : payload.models.embedding.providers.find((item) => item.id === record.id);
  const body = kind === "chat"
    ? {
      kind,
      revision: signature.revision,
      connection: selectedDraft,
    }
    : {
      kind,
      revision: signature.revision,
      provider: selectedDraft,
      settings: payload.models.embedding.settings,
    };
  setModelEditorLocked(false);
  status.textContent = "正在探测精确草稿…";
  delete status.dataset.tone;
  const started = performance.now();
  try {
    const result = await probeModelConnection(body);
    if (!modelOperations.isProbeCurrent(generation)) return;
    const applied = applyProbeResult(state, signature, {
      ...result,
      latency_ms: Math.round(performance.now() - started),
    });
    state = applied.state;
    renderRouteList();
    if (probeRequestVisible(signature)) {
      renderProbeStatus(selectedRecord(state, signature.kind));
    }
  } catch (error) {
    if (!modelOperations.isProbeCurrent(generation)) return;
    const modelError = error as ModelApiError;
    if (modelError.status === 409 && modelError.details?.latest) {
      state = receiveRemoteSnapshot(state, modelError.details.latest);
      render();
    }
    if (probeRequestVisible(signature)) {
      if (probeSignatureMatches(state, signature)) {
        status.textContent = modelError.details?.error || modelError.message || "探测失败";
        status.dataset.tone = "error";
      } else {
        renderProbeStatus(selectedRecord(state, signature.kind));
      }
    }
  } finally {
    modelOperations.finishProbe(generation);
    setModelEditorLocked(false);
  }
}

function retainSelection(next: EditorState, previous: EditorState | null): EditorState {
  for (const kind of ["chat", "embedding"] as const) {
    const id = previous?.selected?.[kind];
    const items = kind === "chat" ? next.models.chat.connections : next.models.embedding.providers;
    if (id && items.some((item) => item.id === id)) next.selected[kind] = id;
  }
  next.activeRoute = previous?.activeRoute || "chat";
  return next;
}

async function saveModels() {
  if (!state || modelOperations.saveInFlight) return;
  const save = beginModelSave();
  if (save === null) return;
  const { generation } = save;
  snapshotRequestGate.invalidate();
  setModelEditorLocked(true);
  setStatus("正在验证并热重载模型路由…");
  try {
    const result = await updateModelConfig(
      toModelConfigPayload(state),
    ) as ModelConfigUpdateResponse;
    state = retainSelection(hydrateModelConfig(result.snapshot), state);
    render();
    setStatus("模型路由已保存并热重载。", "success");
    notify("模型路由已保存", "success");
  } catch (error) {
    const modelError = error as ModelApiError;
    if (modelError.status === 409 && modelError.details?.error === "revision_conflict") {
      state = receiveRemoteSnapshot(state, modelError.details.latest);
      render();
      setStatus("保存被拒绝：远端已有更新。", "error");
    } else if (Array.isArray(modelError.details?.errors)) {
      state = mapServerFieldErrors(state, modelError.details.errors);
      render();
      setStatus("请修正标记的模型字段。", "error");
    } else {
      setStatus(modelError.details?.error || modelError.message || "模型保存失败。", "error");
    }
  } finally {
    if (modelOperations.finishSave(generation)) {
      setModelEditorLocked(false);
    }
  }
}

async function fetchModelSnapshot(remote = false) {
  if (modelOperations.saveInFlight) return;
  await applyLatestSnapshotRequest({
    gate: snapshotRequestGate,
    request: async () => await fetchModelConfig() as RawSnapshot,
    blocked: () => modelOperations.saveInFlight,
    apply: (snapshot) => {
      if (state?.dirty) state = receiveRemoteSnapshot(state, snapshot);
      else if (remote && state) state = receiveRemoteSnapshot(state, snapshot);
      else state = retainSelection(hydrateModelConfig(snapshot), state);
      render();
    },
  });
}

async function loadModelSettings() {
  setStatus("正在读取模型配置…");
  try {
    const loaded = await loadIndependentModelResources({
      gate: snapshotRequestGate,
      descriptorGate: descriptorRequestGate,
      snapshotRequest: async () => await fetchModelConfig() as RawSnapshot,
      descriptorRequest: async () => await fetchModelConnectionTypes() as unknown as ConnectionTypesPayload,
      blocked: () => modelOperations.saveInFlight || Boolean(state?.dirty),
      onSnapshotBlocked: (snapshot) => {
        if (!state?.dirty || modelOperations.saveInFlight) return;
        state = receiveRemoteSnapshot(state, snapshot);
        render();
      },
      applySnapshot: (snapshot) => {
        state = retainSelection(hydrateModelConfig(snapshot), state);
        render();
      },
      installDescriptors: (descriptors) => {
        connectionTypes = descriptors;
        if (state && !modelOperations.saveInFlight) render();
      },
    });
    return loaded;
  } catch (error) {
    setStatus((error as Error).message || "无法读取模型配置。", "error");
    return false;
  }
}

function confirmLeave() {
  return !state?.dirty || window.confirm("模型路由有未保存的更改，确定离开吗？");
}

function bindEvents() {
  document.querySelectorAll<HTMLElement>("[data-popup-model-route]").forEach((tab) => {
    tab.addEventListener("click", () => {
      if (modelMutationBlocked()) return;
      const route = tab.dataset.popupModelRoute;
      if (route !== "chat" && route !== "embedding" && route !== "runtime") return;
      state.activeRoute = route;
      byId("popupModelRouteLayout").classList.remove("is-detail");
      render();
    });
  });
  byId("popupModelAddConnection").addEventListener("click", addConnection);
  byId("popupModelRemoveConnection").addEventListener("click", removeSelected);
  byId("popupModelMoveUp").addEventListener("click", () => moveSelected(-1));
  byId("popupModelMoveDown").addEventListener("click", () => moveSelected(1));
  byId("popupModelDetailBack").addEventListener("click", () => {
    byId("popupModelRouteLayout").classList.remove("is-detail");
    focusSelectedRouteControl();
  });
  byId("popupModelSaveButton").addEventListener("click", () => void saveModels());
  byId("popupModelProbeButton").addEventListener("click", () => void probeSelected());
  byId("popupModelReloadRemote").addEventListener("click", () => {
    if (modelMutationBlocked()) return;
    if (!state?.remoteUpdate || !window.confirm("放弃当前草稿并加载远端模型配置？")) return;
    state = retainSelection(hydrateModelConfig(state.remoteUpdate.snapshot), state);
    render();
  });
  byId("popupModelTypeSearch").addEventListener("input", renderConnectionTypes);
  byId("popupModelConnectionTypeGroups").addEventListener("click", (event) => {
    const button = (event.target as HTMLElement | null)?.closest<HTMLElement>("[data-model-type]");
    if (button?.dataset.modelType) changeType(button.dataset.modelType);
  });
  byId("popupModelConnectionTypeGroups").addEventListener("keydown", moveTypeOptionFocus);
  byId("popupModelInspectorFields").addEventListener("input", (event) => {
    const target = event.target as HTMLInputElement | HTMLSelectElement;
    const field = target.dataset.modelField;
    if (field) updateField(field, target);
  });
  byId("popupModelDescriptorFields").addEventListener("input", (event) => {
    const target = event.target as HTMLInputElement | HTMLSelectElement;
    const field = target.dataset.modelField;
    if (field && target.tagName !== "SELECT") updateField(field, target);
  });
  byId("popupModelDescriptorFields").addEventListener("change", (event) => {
    const target = event.target as HTMLInputElement | HTMLSelectElement;
    const field = target.dataset.modelField;
    if (field) updateField(field, target);
  });
  byId("popupModelCredentialEditor").addEventListener("click", (event) => {
    const action = (event.target as HTMLElement | null)
      ?.closest<HTMLElement>("[data-model-credential-action]")
      ?.dataset.modelCredentialAction;
    if (action) updateCredential(action);
  });
  byId("popupModelCredentialEditor").addEventListener("input", (event) => {
    const target = event.target as HTMLInputElement;
    const kind = state.activeRoute;
    if (target.id === "popupModelCredentialValue" && kind !== "runtime") {
      const record = selectedRecord(state, kind);
      if (record) updateCredential(record.credential.action, target.value, false);
    }
  });
  byId("popupModelRouteList").addEventListener("click", (event) => {
    const id = (event.target as HTMLElement | null)
      ?.closest<HTMLElement>("[data-model-select]")
      ?.dataset.modelSelect;
    if (id) selectRecord(id);
  });
  byId("popupModelRouteList").addEventListener("keydown", (event) => {
    const keyboardEvent = event as KeyboardEvent;
    const row = (keyboardEvent.target as HTMLElement | null)
      ?.closest<HTMLElement>("[data-model-record-id]");
    if (!row || !keyboardEvent.altKey || !["ArrowUp", "ArrowDown"].includes(keyboardEvent.key)) return;
    if (modelMutationBlocked() || routeLocked(state.activeRoute)) return;
    keyboardEvent.preventDefault();
    if (row.dataset.modelRecordId) selectRecord(row.dataset.modelRecordId, false);
    moveSelected(keyboardEvent.key === "ArrowUp" ? -1 : 1);
  });
  byId("popupModelRouteList").addEventListener("dragstart", (event) => {
    const dragEvent = event as DragEvent;
    const row = (dragEvent.target as HTMLElement | null)
      ?.closest<HTMLElement>("[data-model-record-id]");
    if (!row || modelMutationBlocked() || routeLocked(state.activeRoute)) {
      dragEvent.preventDefault();
      return;
    }
    draggedId = row.dataset.modelRecordId || "";
    row.classList.add("is-dragging");
    if (dragEvent.dataTransfer) dragEvent.dataTransfer.effectAllowed = "move";
  });
  byId("popupModelRouteList").addEventListener("dragover", (event) => event.preventDefault());
  byId("popupModelRouteList").addEventListener("drop", (event) => {
    const dragEvent = event as DragEvent;
    dragEvent.preventDefault();
    const kind = state.activeRoute;
    if (kind === "runtime" || modelMutationBlocked() || routeLocked(kind)) return;
    const target = (dragEvent.target as HTMLElement | null)
      ?.closest<HTMLElement>("[data-model-record-id]");
    if (!target || !draggedId) return;
    const targetIndex = activeItems().findIndex((item) => item.id === target.dataset.modelRecordId);
    state = moveRouteItem(state, kind, draggedId, targetIndex);
    const moved = draggedId;
    draggedId = "";
    render();
    focusMovedRow(moved);
  });
  byId("popupModelRouteList").addEventListener("dragend", () => {
    draggedId = "";
    document.querySelectorAll(".model-route-row.is-dragging").forEach((row) => row.classList.remove("is-dragging"));
  });
  byId("popupModelEmbeddingEnabled").addEventListener("change", (event) => {
    const target = event.target as HTMLInputElement;
    if (modelMutationBlocked()) return;
    if (modelControlLocked("models.embedding.enabled")) {
      renderEmbeddingSettings();
      return;
    }
    const providersLocked = Boolean(routeLocked("embedding"));
    if (!target.checked && state.models.embedding.providers.length && !providersLocked) {
    if (!window.confirm("停用 Embedding 会清空当前 Provider 路由。继续吗？")) {
        target.checked = true;
        return;
      }
      state = updateRouteSetting(state, "embedding", "enabled", false);
      state.models.embedding.providers = [];
      state.selected.embedding = "";
    } else {
      state = updateRouteSetting(state, "embedding", "enabled", target.checked);
      if (
        target.checked
        && state.models.embedding.providers.length === 0
        && !providersLocked
      ) addConnection();
    }
    render();
  });
  for (const [id, field, kind, path] of [
    ["popupModelEmbeddingModel", "model", "text", "models.embedding.settings.model"],
    ["popupModelEmbeddingDimension", "output_dimensionality", "number", "models.embedding.settings.output_dimensionality"],
    ["popupModelEmbeddingSimilarity", "similarity_threshold", "number", "models.embedding.settings.similarity_threshold"],
  ] as const) {
    byId(id).addEventListener("input", (event) => {
      const target = event.target as HTMLInputElement;
      if (modelMutationBlocked()) return;
      if (modelControlLocked(path)) return;
      const value = kind === "number" ? Number(target.value) : target.value;
      state = updateRouteSetting(state, "embedding", field, value);
      setStatus("有未保存的模型更改。");
    });
  }
  byId("popupModelEmbeddingMultimodal").addEventListener("change", (event) => {
    const target = event.target as HTMLInputElement;
    if (modelMutationBlocked()) return;
    if (modelControlLocked("models.embedding.settings.multimodal_enabled")) return;
    state = updateRouteSetting(state, "embedding", "multimodal_enabled", target.checked);
    setStatus("有未保存的模型更改。");
  });
  byId("popupModelChatConcurrency").addEventListener("input", (event) => {
    const target = event.target as HTMLInputElement;
    if (modelMutationBlocked()) return;
    if (modelControlLocked("models.chat.concurrency")) return;
    state = updateRouteSetting(state, "chat", "concurrency", Number(target.value));
    setStatus("有未保存的模型更改。");
  });
  byId("popupModelChatTimeout").addEventListener("input", (event) => {
    const target = event.target as HTMLInputElement;
    if (modelMutationBlocked()) return;
    if (modelControlLocked("models.chat.timeout_seconds")) return;
    state = updateRouteSetting(state, "chat", "timeout_seconds", Number(target.value));
    setStatus("有未保存的模型更改。");
  });
  byId("popupModelMigrationPanel").addEventListener("click", (event) => {
    if (modelMutationBlocked()) return;
    const button = (event.target as HTMLElement | null)
      ?.closest<HTMLElement>("[data-migration-action]");
    const migrationId = button?.dataset.migrationId;
    const migrationAction = button?.dataset.migrationAction;
    if (!migrationId || !migrationAction) return;
    state = setMigrationResolution(
      state,
      migrationId,
      migrationResolution(migrationAction),
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
    const detail = (event as CustomEvent<{ type?: string }>).detail;
    if (detail?.type && detail.type !== CONFIG_RELOADED_TYPE) return;
    if (modelOperations.saveInFlight) return;
    void fetchModelSnapshot(true).catch(() => {});
  });
}

const LOCAL_OLLAMA_EMBEDDING_DEFAULTS = Object.freeze({
  id: "embedding-local-ollama",
  name: "本地 Ollama",
  model: "bge-m3",
  output_dimensionality: 1024,
  base_url: "http://127.0.0.1:11434/v1",
});

/**
 * Guarded convenience path for an empty route or one existing Ollama provider.
 * A configured embedding route is never replaced by this one-click action.
 */
export async function enableLocalOllamaEmbeddingRoute() {
  if (!initialized) initPopupModelSettings();
  if (modelOperations.saveInFlight) {
    throw new Error("已有模型保存正在进行。");
  }
  if (state?.dirty) {
    throw new Error("请先保存或重新加载未保存的模型更改。");
  }
  const startedSaveGeneration = modelOperations.saveGeneration;
  const loaded = await loadModelSettings();
  if (!state || !modelOperations.canStartSaveAfterLoad({
    startedSaveGeneration,
    loadResult: loaded || null,
    state,
  })) {
    throw new Error(
      "加载期间权威模型路由已变化或草稿已被编辑；未写入任何更改。",
    );
  }

  const descriptor = descriptorFor("ollama");
  const prepared = prepareLocalOllamaEmbedding(
    state,
    descriptor,
    LOCAL_OLLAMA_EMBEDDING_DEFAULTS,
  );
  const save = beginModelSave();
  if (save === null) {
    throw new Error("已有模型保存正在进行。");
  }
  const { generation } = save;
  snapshotRequestGate.invalidate();
  setModelEditorLocked(true);
  setStatus("正在启用本地 Ollama Embedding…");
  try {
    const result = await updateModelConfig(
      toModelConfigPayload(prepared),
    ) as ModelConfigUpdateResponse;
    state = retainSelection(hydrateModelConfig(result.snapshot), prepared);
    state.activeRoute = "embedding";
    render();
    setStatus("本地 Ollama Embedding 已启用。", "success");
    notify("本地 Ollama Embedding 已启用", "success");
    return result;
  } catch (error) {
    const modelError = error as ModelApiError;
    if (modelError.status === 409 && modelError.details?.error === "revision_conflict") {
      if (modelError.details?.latest) {
        state = receiveRemoteSnapshot(state, modelError.details.latest);
      }
      render();
      setStatus("启用被拒绝：模型配置已在其他位置更新。请检查后重试。", "error");
    } else if (Array.isArray(modelError.details?.errors)) {
      state = mapServerFieldErrors(prepared, modelError.details.errors);
      render();
      setStatus("本地 Ollama 设置未通过模型配置验证。", "error");
    } else {
      setStatus(
        modelError.details?.error || modelError.message || "无法启用本地 Ollama Embedding。",
        "error",
      );
    }
    throw error;
  } finally {
    if (modelOperations.finishSave(generation)) {
      setModelEditorLocked(false);
    }
  }
}

export function initPopupModelSettings(options: InitModelSettingsOptions = {}) {
  if (typeof options.onToast === "function") notify = options.onToast;
  if (!initialized && byId("popupModelRouteTabs")) {
    bindEvents();
    initialized = true;
  }
  return {
    open: async () => {
      if (state?.dirty) {
        render();
        return true;
      }
      return loadModelSettings();
    },
    isDirty: () => Boolean(state?.dirty),
    confirmLeave,
    reload: () => fetchModelSnapshot(false),
    enableLocalOllamaEmbedding: enableLocalOllamaEmbeddingRoute,
  };
}
