const sharedStateUrl = new URL("/web/shared/model-config-state.js", import.meta.url);
const sharedRenderUrl = new URL("/web/shared/model-config-render.js", import.meta.url);
const sharedStateVersion = new URL(import.meta.url).searchParams.get("v");
if (sharedStateVersion) {
  sharedStateUrl.searchParams.set("v", sharedStateVersion);
  sharedRenderUrl.searchParams.set("v", sharedStateVersion);
}

const {
  appendRouteItem,
  applyLatestSnapshotRequest,
  applyProbeResult,
  applyPreset,
  changeConnectionType,
  changePreset,
  circuitView,
  createLatestRequestGate,
  createProbeSignature,
  hasUnverifiedChanges,
  hydrateModelConfig,
  mapServerFieldErrors,
  moveRouteItem,
  probeSignatureMatches,
  receiveRemoteSnapshot,
  removeRouteItem,
  selectRouteItem,
  selectedRecord,
  setMigrationResolution,
  toModelConfigPayload,
  unverifiedConnections,
  updateRouteField,
  updateRouteSetting,
} = await import(sharedStateUrl.href);

const {
  applyTypeOptionRovingTabindex,
  disabledMarkup,
  escapeHtml,
  moveTypeOptionFocus: sharedMoveTypeOptionFocus,
  renderConnectionTypeGroups,
  renderCredentialEditor,
  renderDescriptorField: sharedRenderDescriptorField,
} = await import(sharedRenderUrl.href);

const MODEL_CONFIG_API = "/api/model-config";
const CONNECTION_TYPES_API = "/api/model-connection-types";
const MODEL_PROBE_API = "/api/model-config/probe";
const MODEL_PROBE_TIMEOUT_MS = 60_000;
const CONFIG_RELOADED_TYPE = "config_reloaded";
const ROUTE_OVERRIDE_PATHS = {
  chat: "models.chat.connections",
  embedding: "models.embedding.providers",
};

let state = null;
let connectionTypes = { connection_types: [], groups: [] };
let draggedId = "";
let probeGeneration = 0;
let saveInFlight = false;
let saveGeneration = 0;
let circuitCountdownTimer = null;
const snapshotRequestGate = createLatestRequestGate();

const byId = (id) => document.getElementById(id);

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
  return !state || saveInFlight;
}

function setModelEditorLocked(locked) {
  const boundary = byId("modelEditorBoundary");
  if (!boundary) return;
  boundary.disabled = locked;
  boundary.inert = locked;
  boundary.setAttribute("aria-busy", locked ? "true" : "false");
  byId("modelSaveButton").disabled = locked;
}

function probeRequestVisible(signature) {
  return Boolean(
    state
    && state.activeRoute === signature.kind
    && state.selected?.[signature.kind] === signature.id,
  );
}

function modelApiPath(url) {
  return url.startsWith("/api/") ? url.slice(4) : url;
}

async function requestModelJson(url, options = {}) {
  const bridge = window.OpenBiliClawDesktopApi;
  if (bridge?.requestJsonStrict) {
    return bridge.requestJsonStrict(modelApiPath(url), options);
  }
  const {
    timeoutMs = 60000,
    timeoutMessage = "",
    ...fetchOptions
  } = options;
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      ...fetchOptions,
      credentials: "same-origin",
      headers: { "X-OBC-Auth": "1", ...(fetchOptions.headers || {}) },
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
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(timeoutMessage || `${url} 请求超时，请稍后重试。`);
    }
    throw error;
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
    const circuit = circuitView(record);
    return { label: circuit?.label || record.circuit.failure_kind || "熔断已打开", tone: "warning" };
  }
  if (record?.probe?.ok === true) return { label: "探测通过", tone: "success" };
  if (record?.probe?.ok === false) return { label: record.probe.error_code || "探测失败", tone: "error" };
  return { label: "尚未探测", tone: "" };
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
  byId("modelRouteEyebrow").textContent = "调用顺序";
  byId("modelRouteTitle").textContent = kind === "chat" ? "Chat 路由" : "Embedding 路由";
  byId("modelAddConnection").textContent = kind === "chat" ? "添加连接" : "添加 Provider";
  byId("modelRouteHelp").textContent = kind === "chat"
    ? "从上到下依次调用；第 1 项为主连接，最多 10 项。"
    : "从上到下依次回退；共用上方模型设置，最多 10 项。";
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
        <span class="model-route-drag-handle" aria-label="拖拽排序" title="${locked ? "顺序由只读覆盖配置提供" : "拖拽排序"}" aria-disabled="${locked ? "true" : "false"}">⋮⋮</span>
        <button class="model-route-row-copy" type="button" data-model-select="${escapeHtml(record.id)}">
          <strong>${escapeHtml(derivedRole(index))} · ${escapeHtml(record.name || "未命名连接")}</strong>
          <span>${escapeHtml(descriptor?.label || record.type)}${preset ? ` / ${escapeHtml(preset.label)}` : ""} · ${escapeHtml(model || "未设置模型")}</span>
        </button>
        <span class="model-route-health" data-tone="${health.tone}">${escapeHtml(health.label)}</span>
      </div>`;
  }).join("") || `<p class="model-route-empty">${kind === "chat" ? "尚未添加 Chat 连接。" : "尚未添加 Embedding Provider。"}</p>`;
}

function renderConnectionTypes() {
  const record = selectedRecord(state, state.activeRoute);
  if (!record) return;
  const locked = Boolean(routeLocked(state.activeRoute));
  const host = byId("modelConnectionTypeGroups");
  host.innerHTML = renderConnectionTypeGroups({
    groups: connectionTypes.groups,
    record,
    kind: state.activeRoute,
    locked,
    query: byId("modelTypeSearch")?.value || "",
  });
  applyTypeOptionRovingTabindex(host);
}

function moveTypeOptionFocus(event) {
  sharedMoveTypeOptionFocus(event);
}

function focusSelectedTypeOption() {
  const record = selectedRecord(state, state.activeRoute);
  if (!record) return;
  window.requestAnimationFrame(() => {
    byId("modelConnectionTypeGroups")
      ?.querySelector(`[data-model-type="${CSS.escape(record.type)}"]`)
      ?.focus();
  });
}

function renderDescriptorField(record, descriptor, field) {
  return sharedRenderDescriptorField({
    record,
    descriptor,
    field,
    kind: state.activeRoute,
    locked: Boolean(routeLocked(state.activeRoute)),
    errorMarkup,
    fieldClass: "settings-field",
  });
}

function renderCredential(record, descriptor) {
  const host = byId("modelCredentialEditor");
  const rendered = renderCredentialEditor({
    record,
    descriptor,
    kind: state.activeRoute,
    locked: Boolean(routeLocked(state.activeRoute)),
    errorMarkup,
    fieldClass: "settings-field",
    credentialValueId: "modelCredentialValue",
  });
  host.hidden = rendered.hidden;
  host.innerHTML = rendered.html;
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
  const circuit = circuitView(record);
  const circuitChip = circuit
    ? `<span class="model-circuit-chip" title="${escapeHtml(circuit.failureKind || "熔断打开")}">${escapeHtml(circuit.label)}</span>`
    : "";
  const unverified = hasUnverifiedChanges(state, kind, record.id)
    ? '<p class="model-unverified-warning" role="status">此连接在上次探测通过后被修改，保存前建议重新探测。</p>'
    : "";
  byId("modelInspectorFields").innerHTML = `
    <label class="settings-field full"><span>连接名称</span><input data-model-field="name" value="${escapeHtml(record.name)}" autocomplete="off" required${disabledMarkup(locked)}>${errorMarkup(record.id, "name")}</label>
    <label class="settings-field full"><span>稳定 ID</span><input value="${escapeHtml(record.id)}" readonly aria-readonly="true"><small>排序或改名不会改变此 ID。</small>${errorMarkup(record.id, "id")}</label>
    ${circuitChip ? `<div class="settings-field full">${circuitChip}</div>` : ""}
    ${unverified ? `<div class="settings-field full">${unverified}</div>` : ""}`;
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
  const dimensions = probe.observed_dimension ? ` · ${probe.observed_dimension} 维` : "";
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
    <div class="model-runtime-card"><span>Chat 路由</span><strong>${state.models.chat.connections.length} 个连接</strong></div>
    <div class="model-runtime-card"><span>Embedding 路由</span><strong>${state.models.embedding.providers.length} 个 Provider · ${state.models.embedding.enabled ? "已启用" : "已停用"}</strong></div>
    <div class="model-runtime-card"><span>当前健康状态</span><strong>${healthy} 个探测通过 · ${open} 个熔断打开</strong></div>`;
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

function syncCircuitCountdown() {
  const all = state
    ? [...state.models.chat.connections, ...state.models.embedding.providers]
    : [];
  const anyOpen = all.some((record) => {
    const view = circuitView(record);
    return view && !view.permanent && view.retrySeconds !== null;
  });
  if (anyOpen && circuitCountdownTimer === null) {
    circuitCountdownTimer = window.setInterval(() => {
      if (!state) return;
      renderRouteList();
      renderInspector();
    }, 1000);
  } else if (!anyOpen && circuitCountdownTimer !== null) {
    window.clearInterval(circuitCountdownTimer);
    circuitCountdownTimer = null;
  }
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
  syncCircuitCountdown();
  byId("modelSaveButton").disabled = saveInFlight;
  setStatus(state.dirty ? "有未保存的模型更改。" : `模型配置已同步 · ${state.revision.slice(0, 12)}`);
}

function focusMovedRow(id) {
  window.requestAnimationFrame(() => {
    const row = document.querySelector(`[data-model-record-id="${CSS.escape(id)}"]`);
    if (row) row.focus();
  });
}

function focusNarrowDetail() {
  const layout = document.querySelector(".layout");
  const narrowViewport = window.matchMedia("(max-width: 820px)").matches;
  const narrowContent = layout && layout.getBoundingClientRect().width <= 940;
  if (!narrowViewport && !narrowContent) return;
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
  if (modelMutationBlocked()) return;
  if (routeLocked(state.activeRoute)) return;
  const record = selectedRecord(state, state.activeRoute);
  if (!record) return;
  const target = selectedIndex() + delta;
  state = moveRouteItem(state, state.activeRoute, record.id, target);
  render();
  focusMovedRow(record.id);
}

function selectRecord(id, openDetail = true) {
  if (modelMutationBlocked()) return;
  state = selectRouteItem(state, state.activeRoute, id);
  byId("modelRouteLayout").classList.toggle("is-detail", openDetail);
  renderRouteList();
  renderInspector();
  if (openDetail) {
    window.scrollTo({ top: 0, behavior: "auto" });
    focusNarrowDetail();
  }
}

function addConnection() {
  if (modelMutationBlocked()) return;
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
  if (modelMutationBlocked()) return;
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
  if (modelMutationBlocked()) return;
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
  focusSelectedTypeOption();
}

function updateField(field, target) {
  if (modelMutationBlocked()) return;
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
  if (modelMutationBlocked()) return;
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
  if (!state || saveInFlight) return;
  const kind = state.activeRoute;
  const record = selectedRecord(state, kind);
  if (!record || kind === "runtime") return;
  const generation = ++probeGeneration;
  const signature = createProbeSignature(state, kind, record.id);
  const button = byId("modelProbeButton");
  const status = byId("modelProbeStatus");
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
  button.disabled = true;
  status.textContent = "正在探测精确草稿…";
  delete status.dataset.tone;
  const started = performance.now();
  try {
    const result = await requestModelJson(MODEL_PROBE_API, {
      method: "POST",
      timeoutMs: MODEL_PROBE_TIMEOUT_MS,
      timeoutMessage: "模型连接探测超时；探测不会写入配置，请检查 API endpoint 与网络后重试。",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (generation !== probeGeneration) return;
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
    if (generation !== probeGeneration) return;
    if (error.status === 409 && error.details?.latest) {
      state = receiveRemoteSnapshot(state, error.details.latest);
      render();
    }
    if (probeRequestVisible(signature)) {
      if (probeSignatureMatches(state, signature)) {
        status.textContent = error.details?.error || error.message || "探测失败";
        status.dataset.tone = "error";
      } else {
        renderProbeStatus(selectedRecord(state, signature.kind));
      }
    }
  } finally {
    if (generation === probeGeneration) button.disabled = saveInFlight;
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
  if (!state || saveInFlight) return;
  const unverified = unverifiedConnections(state);
  if (unverified.length) {
    const names = unverified.map((item) => item.name).join("、");
    showToast(`提示：${names} 在上次探测后被修改，保存未验证的更改。`);
  }
  const generation = ++saveGeneration;
  saveInFlight = true;
  snapshotRequestGate.invalidate();
  setModelEditorLocked(true);
  setStatus("正在验证并热重载模型路由…");
  try {
    const result = await requestModelJson(MODEL_CONFIG_API, {
      method: "PUT",
      timeoutMs: 60000,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(toModelConfigPayload(state)),
    });
    state = retainSelection(hydrateModelConfig(result.snapshot), state);
    render();
    setStatus("模型路由已保存并热重载。", "success");
    showToast("模型路由已保存");
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
    if (generation === saveGeneration) {
      saveInFlight = false;
      setModelEditorLocked(false);
    }
  }
}

async function fetchModelSnapshot(remote = false) {
  if (saveInFlight) return;
  await applyLatestSnapshotRequest({
    gate: snapshotRequestGate,
    request: () => requestModelJson(MODEL_CONFIG_API),
    blocked: () => saveInFlight,
    apply: (snapshot) => {
      if (remote && state) state = receiveRemoteSnapshot(state, snapshot);
      else state = retainSelection(hydrateModelConfig(snapshot), state);
      render();
    },
  });
}

async function loadModelSettings() {
  setStatus("正在读取模型配置…");
  try {
    let descriptorsReady;
    const snapshotLoad = applyLatestSnapshotRequest({
      gate: snapshotRequestGate,
      request: async () => {
        const snapshotReady = requestModelJson(MODEL_CONFIG_API);
        descriptorsReady = requestModelJson(CONNECTION_TYPES_API).then((descriptors) => {
          connectionTypes = descriptors;
          if (state && !saveInFlight) render();
        });
        const [snapshot] = await Promise.all([
          snapshotReady,
          descriptorsReady,
        ]);
        return snapshot;
      },
      blocked: () => saveInFlight,
      apply: (snapshot) => {
        state = hydrateModelConfig(snapshot);
        state.activeRoute = "chat";
      },
    });
    await Promise.all([snapshotLoad, descriptorsReady]);
    if (state && !saveInFlight) render();
  } catch (error) {
    setStatus(error.message || "无法读取模型配置。", "error");
  }
}

function confirmLeave() {
  return !state?.dirty || window.confirm("模型路由有未保存的更改，确定离开吗？");
}

function bindEvents() {
  document.querySelectorAll("[data-model-route]").forEach((tab) => {
    tab.addEventListener("click", () => {
      if (modelMutationBlocked()) return;
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
    if (modelMutationBlocked()) return;
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
    if (modelMutationBlocked() || routeLocked(state.activeRoute)) return;
    event.preventDefault();
    selectRecord(row.dataset.modelRecordId, false);
    moveSelected(event.key === "ArrowUp" ? -1 : 1);
  });
  byId("modelRouteList").addEventListener("dragstart", (event) => {
    const row = event.target.closest("[data-model-record-id]");
    if (!row || modelMutationBlocked() || routeLocked(state.activeRoute)) {
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
    if (modelMutationBlocked() || routeLocked(state.activeRoute)) return;
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
    if (modelMutationBlocked()) return;
    if (modelControlLocked("models.embedding.enabled")) {
      renderEmbeddingSettings();
      return;
    }
    const providersLocked = Boolean(routeLocked("embedding"));
    if (!event.target.checked && state.models.embedding.providers.length && !providersLocked) {
    if (!window.confirm("停用 Embedding 会清空当前 Provider 路由。继续吗？")) {
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
      if (modelMutationBlocked()) return;
      if (modelControlLocked(path)) return;
      const value = kind === "number" ? Number(event.target.value) : event.target.value;
      state = updateRouteSetting(state, "embedding", field, value);
      setStatus("有未保存的模型更改。");
    });
  }
  byId("modelEmbeddingMultimodal").addEventListener("change", (event) => {
    if (modelMutationBlocked()) return;
    if (modelControlLocked("models.embedding.settings.multimodal_enabled")) return;
    state = updateRouteSetting(state, "embedding", "multimodal_enabled", event.target.checked);
    setStatus("有未保存的模型更改。");
  });
  byId("modelChatConcurrency").addEventListener("input", (event) => {
    if (modelMutationBlocked()) return;
    if (modelControlLocked("models.chat.concurrency")) return;
    state = updateRouteSetting(state, "chat", "concurrency", Number(event.target.value));
    setStatus("有未保存的模型更改。");
  });
  byId("modelChatTimeout").addEventListener("input", (event) => {
    if (modelMutationBlocked()) return;
    if (modelControlLocked("models.chat.timeout_seconds")) return;
    state = updateRouteSetting(state, "chat", "timeout_seconds", Number(event.target.value));
    setStatus("有未保存的模型更改。");
  });
  byId("modelMigrationPanel").addEventListener("click", (event) => {
    if (modelMutationBlocked()) return;
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
    if (saveInFlight) return;
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
