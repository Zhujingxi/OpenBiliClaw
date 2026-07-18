/**
 * DOM-free state transitions for every web model-route editor.
 *
 * Public snapshots contain credential status only.  Hydration deliberately
 * replaces that status with an explicit write action and keeps the safe status
 * beside it, so a masked or raw persisted secret can never enter a draft.
 */

const MAX_ROUTE_ITEMS = 10;
const CHAT_FIELDS = [
  "model",
  "preset",
  "base_url",
  "credential",
  "api_mode",
  "reasoning_effort",
  "http_referer",
  "x_title",
  "num_ctx",
];
const EMBEDDING_FIELDS = ["preset", "base_url", "credential"];
const PROBE_FINGERPRINT_FIELDS = new Set([
  "name",
  "type",
  "model",
  "preset",
  "base_url",
  "credential",
  "api_mode",
  "reasoning_effort",
  "http_referer",
  "x_title",
  "num_ctx",
]);
const OVERRIDE_CONTROL_PATHS = [
  "models.chat.connections",
  "models.chat.concurrency",
  "models.chat.timeout_seconds",
  "models.embedding.enabled",
  "models.embedding.settings.model",
  "models.embedding.settings.output_dimensionality",
  "models.embedding.settings.similarity_threshold",
  "models.embedding.settings.multimodal_enabled",
  "models.embedding.providers",
];

function clone(value) {
  if (typeof structuredClone === "function") return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

function valuesEqual(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function createLatestRequestGate() {
  let generation = 0;
  return {
    begin() {
      generation += 1;
      return generation;
    },
    invalidate() {
      generation += 1;
    },
    isCurrent(candidate) {
      return candidate === generation;
    },
  };
}

async function applyLatestRequestGeneration({
  gate,
  generation,
  request,
  blocked,
  apply,
  onBlocked = null,
}) {
  let value;
  try {
    value = await request();
  } catch (error) {
    if (!gate.isCurrent(generation) || blocked()) return false;
    throw error;
  }
  if (!gate.isCurrent(generation)) return false;
  if (blocked()) {
    if (typeof onBlocked === "function") onBlocked(value);
    return false;
  }
  apply(value);
  return true;
}

export async function applyLatestSnapshotRequest(options) {
  const generation = options.gate.begin();
  return applyLatestRequestGeneration({ ...options, generation });
}

/** Coordinate one revisioned save with exact-probe request ownership. */
export function createModelOperationGate() {
  let saveGeneration = 0;
  let saveInFlight = false;
  let probeGeneration = 0;
  let probeInFlight = false;

  return {
    get saveGeneration() {
      return saveGeneration;
    },
    get saveInFlight() {
      return saveInFlight;
    },
    get probeInFlight() {
      return probeInFlight;
    },
    beginProbe() {
      probeGeneration += 1;
      probeInFlight = true;
      return probeGeneration;
    },
    isProbeCurrent(candidate) {
      return candidate === probeGeneration;
    },
    finishProbe(candidate) {
      if (candidate !== probeGeneration) return false;
      probeInFlight = false;
      return true;
    },
    beginSave() {
      if (saveInFlight) return null;
      const invalidatedProbe = probeInFlight;
      saveGeneration += 1;
      saveInFlight = true;
      probeGeneration += 1;
      probeInFlight = false;
      return { generation: saveGeneration, invalidatedProbe };
    },
    finishSave(candidate) {
      if (!saveInFlight || candidate !== saveGeneration) return false;
      saveInFlight = false;
      return true;
    },
    canStartSaveAfterLoad({ startedSaveGeneration, loadResult, state }) {
      return Boolean(
        loadResult?.snapshotApplied === true
        && loadResult?.descriptorsInstalled === true
        && startedSaveGeneration === saveGeneration
        && !saveInFlight
        && !state?.dirty,
      );
    },
    controlState() {
      return {
        editorLocked: saveInFlight,
        saveDisabled: saveInFlight,
        probeDisabled: saveInFlight || probeInFlight,
      };
    },
  };
}

/**
 * Load snapshot and descriptors independently, then recheck ownership after
 * both siblings settle. A locally blocked snapshot can still be retained as a
 * remote update while the winning descriptor registry installs normally.
 */
export async function loadIndependentModelResources({
  gate,
  descriptorGate = createLatestRequestGate(),
  snapshotRequest,
  descriptorRequest,
  blocked,
  onSnapshotBlocked = null,
  applySnapshot,
  installDescriptors,
}) {
  const snapshotGeneration = gate.begin();
  const descriptorGeneration = descriptorGate.begin();
  const snapshotLoad = applyLatestRequestGeneration({
    gate,
    generation: snapshotGeneration,
    request: snapshotRequest,
    blocked,
    apply: applySnapshot,
    onBlocked: onSnapshotBlocked,
  });
  const descriptorLoad = applyLatestRequestGeneration({
    gate: descriptorGate,
    generation: descriptorGeneration,
    request: descriptorRequest,
    blocked: () => false,
    apply: installDescriptors,
  });
  const [snapshotOutcome, descriptorOutcome] = await Promise.allSettled([
    snapshotLoad,
    descriptorLoad,
  ]);
  const snapshotOwned = gate.isCurrent(snapshotGeneration);
  const descriptorOwned = descriptorGate.isCurrent(descriptorGeneration);
  const snapshotBlocked = blocked();
  if (snapshotOutcome.status === "rejected" && snapshotOwned) {
    throw snapshotOutcome.reason;
  }
  if (descriptorOutcome.status === "rejected" && descriptorOwned) {
    throw descriptorOutcome.reason;
  }
  return {
    snapshotApplied: snapshotOutcome.status === "fulfilled"
      && snapshotOutcome.value
      && snapshotOwned
      && !snapshotBlocked,
    descriptorsInstalled: descriptorOutcome.status === "fulfilled"
      && descriptorOutcome.value
      && descriptorOwned,
  };
}

function normalizeOverridePath(path) {
  return String(path || "")
    .replace(/\[(\d+)\]/g, ".$1")
    .replace(/^\.+|\.+$/g, "");
}

function buildOverrideLocks(overrides) {
  const entries = (Array.isArray(overrides) ? overrides : []).map((override) => ({
    path: String(override?.path || ""),
    source: String(override?.source || ""),
  }));
  const locks = Object.fromEntries(OVERRIDE_CONTROL_PATHS.map((path) => {
    const normalizedPath = normalizeOverridePath(path);
    const lock = entries.find((override) => {
      const overridePath = normalizeOverridePath(override.path);
      return overridePath && (
        normalizedPath === overridePath
        || normalizedPath.startsWith(`${overridePath}.`)
      );
    });
    return [path, lock ? clone(lock) : null];
  }));
  if (!locks["models.embedding.enabled"] && locks["models.embedding.providers"]) {
    locks["models.embedding.enabled"] = clone(locks["models.embedding.providers"]);
  }
  return locks;
}

function routeKey(kind) {
  if (kind === "chat") return "connections";
  if (kind === "embedding") return "providers";
  throw new Error(`Unknown model route: ${kind}`);
}

function routeItems(state, kind) {
  return state.models[kind][routeKey(kind)];
}

function safeCredentialStatus(raw) {
  const source = ["none", "inline", "env", "oauth"].includes(raw?.source)
    ? raw.source
    : "none";
  return {
    source,
    configured: Boolean(raw?.configured),
    env_name: typeof raw?.env_name === "string" ? raw.env_name : "",
    credential_ref: typeof raw?.credential_ref === "string" ? raw.credential_ref : "",
    oauth_logged_in: Boolean(raw?.oauth_logged_in),
  };
}

function hydrateCredential(raw) {
  return {
    action: "keep",
    value: "",
    status: safeCredentialStatus(raw),
  };
}

function hydrateRecord(raw, kind) {
  const record = {
    id: String(raw?.id || ""),
    name: String(raw?.name || ""),
    type: String(raw?.type || ""),
    preset: String(raw?.preset || ""),
    base_url: String(raw?.base_url || ""),
    credential: hydrateCredential(raw?.credential),
    probe: raw?.probe ? clone(raw.probe) : null,
    circuit: raw?.circuit ? clone(raw.circuit) : { state: "closed" },
  };
  if (kind === "chat") {
    Object.assign(record, {
      model: String(raw?.model || ""),
      api_mode: String(raw?.api_mode || ""),
      reasoning_effort: String(raw?.reasoning_effort || ""),
      http_referer: String(raw?.http_referer || ""),
      x_title: String(raw?.x_title || ""),
      num_ctx: Number.isFinite(Number(raw?.num_ctx)) ? Number(raw.num_ctx) : 0,
    });
  }
  return record;
}

function emptyFieldErrors() {
  return { byConnection: {}, global: [] };
}

function ownFieldErrorBucket(container, key) {
  if (!Object.hasOwn(container, key)) {
    Object.defineProperty(container, key, {
      value: {},
      configurable: true,
      enumerable: true,
      writable: true,
    });
  }
  return container[key];
}

function touchedKey(kind, id, field) {
  return `${kind}:${id}:${field}`;
}

function markChanged(state) {
  state.dirty = true;
  state.fieldErrors = emptyFieldErrors();
  return state;
}

function findIndex(state, kind, id) {
  const index = routeItems(state, kind).findIndex((item) => item.id === id);
  if (index < 0) throw new Error(`Unknown ${kind} connection ID: ${id}`);
  return index;
}

function cleanDraftRecord(record, kind) {
  const hydrated = hydrateRecord(record, kind);
  const action = record?.credential?.action;
  if (["keep", "set", "clear", "env"].includes(action)) {
    hydrated.credential.action = action;
    hydrated.credential.value = ["set", "env"].includes(action)
      ? String(record.credential.value || "")
      : "";
  } else if (!record?.credential?.source && !record?.credential?.status) {
    hydrated.credential.action = "clear";
  }
  return hydrated;
}

export function hydrateModelConfig(snapshot) {
  const source = snapshot && typeof snapshot === "object" ? snapshot : {};
  const models = source.models && typeof source.models === "object" ? source.models : {};
  const chat = models.chat && typeof models.chat === "object" ? models.chat : {};
  const embedding = models.embedding && typeof models.embedding === "object"
    ? models.embedding
    : {};
  const connections = Array.isArray(chat.connections)
    ? chat.connections.map((item) => hydrateRecord(item, "chat"))
    : [];
  const providers = Array.isArray(embedding.providers)
    ? embedding.providers.map((item) => hydrateRecord(item, "embedding"))
    : [];
  const overrides = clone(Array.isArray(source.overrides) ? source.overrides : []);
  return {
    revision: String(source.revision || ""),
    source: String(source.source || ""),
    models: {
      schema_version: Number(models.schema_version) || 1,
      chat: {
        connections,
        concurrency: Number(chat.concurrency) || 4,
        timeout_seconds: Number(chat.timeout_seconds) || 300,
      },
      embedding: {
        enabled: Boolean(embedding.enabled),
        settings: {
          model: String(embedding.settings?.model || ""),
          output_dimensionality: Math.max(
            0,
            Number(embedding.settings?.output_dimensionality) || 0,
          ),
          similarity_threshold: Number.isFinite(Number(embedding.settings?.similarity_threshold))
            ? Number(embedding.settings.similarity_threshold)
            : 0.82,
          multimodal_enabled: Boolean(embedding.settings?.multimodal_enabled),
        },
        providers,
      },
    },
    migration: clone(source.migration || { state: "none", confirmed: true, issues: [] }),
    overrides,
    overrideLocks: buildOverrideLocks(overrides),
    selected: {
      chat: connections[0]?.id || "",
      embedding: providers[0]?.id || "",
    },
    touched: {},
    dirty: false,
    remoteUpdate: null,
    fieldErrors: emptyFieldErrors(),
    migration_resolutions: {},
  };
}

export function selectRouteItem(state, kind, id) {
  findIndex(state, kind, id);
  const next = clone(state);
  next.selected[kind] = id;
  return next;
}

export function appendRouteItem(state, kind, record) {
  const current = routeItems(state, kind);
  if (current.length >= MAX_ROUTE_ITEMS) {
    throw new Error(`${kind} route has a maximum 10 connections.`);
  }
  const next = clone(state);
  const items = routeItems(next, kind);
  const candidate = cleanDraftRecord(record, kind);
  if (!candidate.id) throw new Error("A stable connection ID is required.");
  if (items.some((item) => item.id === candidate.id)) {
    throw new Error(`Connection ID already exists: ${candidate.id}`);
  }
  items.push(candidate);
  next.selected[kind] = candidate.id;
  return markChanged(next);
}

export function removeRouteItem(state, kind, id) {
  const current = routeItems(state, kind);
  const index = findIndex(state, kind, id);
  if (kind === "chat" && current.length <= 1) {
    throw new Error("Chat route must keep at least one connection.");
  }
  if (kind === "embedding" && state.models.embedding.enabled && current.length <= 1) {
    throw new Error("Enabled embedding must keep at least one provider.");
  }
  const next = clone(state);
  const items = routeItems(next, kind);
  items.splice(index, 1);
  if (next.selected[kind] === id) {
    next.selected[kind] = items[Math.min(index, items.length - 1)]?.id || "";
  }
  for (const key of Object.keys(next.touched)) {
    if (key.startsWith(`${kind}:${id}:`)) delete next.touched[key];
  }
  return markChanged(next);
}

export function moveRouteItem(state, kind, id, targetIndex) {
  const current = routeItems(state, kind);
  const from = findIndex(state, kind, id);
  const to = Math.max(0, Math.min(current.length - 1, Number(targetIndex)));
  if (from === to) return clone(state);
  const next = clone(state);
  const items = routeItems(next, kind);
  const [item] = items.splice(from, 1);
  items.splice(to, 0, item);
  return markChanged(next);
}

export function updateRouteField(state, kind, id, field, value) {
  const next = clone(state);
  const index = findIndex(next, kind, id);
  const item = next.models[kind][routeKey(kind)][index];
  if (field === "id") throw new Error("Stable connection IDs cannot be edited.");
  const previousValue = item[field] === undefined ? undefined : clone(item[field]);
  if (field === "credential") {
    const action = String(value?.action || "keep");
    if (!["keep", "set", "clear", "env"].includes(action)) {
      throw new Error(`Unknown credential action: ${action}`);
    }
    item.credential = {
      ...item.credential,
      action,
      value: ["set", "env"].includes(action) ? String(value?.value || "") : "",
    };
  } else {
    item[field] = value;
  }
  if (PROBE_FINGERPRINT_FIELDS.has(field) && !valuesEqual(previousValue, item[field])) {
    item.probe = null;
  }
  next.touched[touchedKey(kind, id, field)] = true;
  return markChanged(next);
}

export function updateRouteSetting(state, kind, field, value) {
  const next = clone(state);
  if (kind === "embedding" && field in next.models.embedding.settings) {
    const changed = !valuesEqual(next.models.embedding.settings[field], value);
    next.models.embedding.settings[field] = value;
    if (changed) {
      for (const provider of next.models.embedding.providers) provider.probe = null;
    }
  } else {
    next.models[kind][field] = value;
  }
  next.touched[`${kind}:settings:${field}`] = true;
  return markChanged(next);
}

export function applyPreset(state, kind, id, presetDefinition, options = {}) {
  const next = clone(state);
  const index = findIndex(next, kind, id);
  const item = routeItems(next, kind)[index];
  item.preset = String(presetDefinition?.id || "");
  for (const [field, value] of Object.entries(presetDefinition?.defaults || {})) {
    const previousDefaults = options.previousPreset?.defaults || {};
    const matchesPreviousDefault = Object.hasOwn(previousDefaults, field)
      && item[field] === previousDefaults[field];
    const isBlank = item[field] === "" || item[field] === null || item[field] === undefined;
    if (
      !next.touched[touchedKey(kind, id, field)]
      && (isBlank || matchesPreviousDefault)
    ) item[field] = value;
  }
  item.probe = null;
  next.touched[touchedKey(kind, id, "preset")] = true;
  return markChanged(next);
}

export function changePreset(
  state,
  kind,
  id,
  descriptor,
  presetDefinition,
  options = {},
) {
  const index = findIndex(state, kind, id);
  const item = routeItems(state, kind)[index];
  const nextPreset = String(presetDefinition?.id || "");
  const allowed = new Set(
    (descriptor?.fields || [])
      .filter((field) => (
        (!field.capabilities?.length || field.capabilities.includes(kind))
        && (!field.presets?.length || field.presets.includes(nextPreset))
      ))
      .map((field) => field.name),
  );
  const possible = kind === "chat" ? CHAT_FIELDS : EMBEDDING_FIELDS;
  const incompatibleFields = possible.filter(
    (field) => field !== "preset" && !allowed.has(field) && isPopulated(field, item[field]),
  );
  if (incompatibleFields.length && !options.confirmed) {
    return { state: clone(state), incompatibleFields, changed: false };
  }
  const next = clone(state);
  const candidate = routeItems(next, kind)[index];
  for (const field of incompatibleFields) clearField(candidate, field);
  const previousPreset = descriptor?.preset_definitions?.find(
    (preset) => preset.id === item.preset,
  );
  return {
    state: applyPreset(next, kind, id, presetDefinition, { previousPreset }),
    incompatibleFields,
    changed: true,
  };
}

function isPopulated(field, value) {
  if (field === "credential") {
    const status = value?.status;
    return Boolean(
      value?.action === "set"
      || value?.action === "env"
      || status?.configured
      || status?.source === "oauth",
    );
  }
  if (typeof value === "string") return Boolean(value.trim());
  if (typeof value === "number") return value !== 0;
  return Boolean(value);
}

function clearField(item, field) {
  if (field === "credential") {
    item.credential = {
      action: "clear",
      value: "",
      status: safeCredentialStatus(null),
    };
  } else if (field === "num_ctx") {
    item[field] = 0;
  } else {
    item[field] = "";
  }
}

export function changeConnectionType(state, kind, id, descriptor, options = {}) {
  const index = findIndex(state, kind, id);
  const item = routeItems(state, kind)[index];
  const previousCategory = String(options.previousDescriptor?.category || "");
  const nextCategory = String(descriptor?.category || "");
  const credentialSemanticsChanged = Boolean(previousCategory)
    && ((previousCategory === "oauth") !== (nextCategory === "oauth"));
  const allowed = new Set(
    (descriptor?.fields || [])
      .filter((field) => (
        (!field.capabilities?.length || field.capabilities.includes(kind))
        && (!field.presets?.length || field.presets.includes(item.preset))
      ))
      .map((field) => field.name),
  );
  const possible = kind === "chat" ? CHAT_FIELDS : EMBEDDING_FIELDS;
  const incompatibleFields = possible.filter(
    (field) => !allowed.has(field) && isPopulated(field, item[field]),
  );
  if (
    credentialSemanticsChanged
    && isPopulated("credential", item.credential)
    && !incompatibleFields.includes("credential")
  ) incompatibleFields.push("credential");
  if (incompatibleFields.length && !options.confirmed) {
    return { state: clone(state), incompatibleFields, changed: false };
  }
  const next = clone(state);
  const candidate = routeItems(next, kind)[index];
  for (const field of possible) {
    if (!allowed.has(field)) clearField(candidate, field);
  }
  if (credentialSemanticsChanged) {
    candidate.credential = nextCategory === "oauth"
      ? {
        action: "keep",
        value: "",
        status: safeCredentialStatus(null),
      }
      : {
        action: "clear",
        value: "",
        status: safeCredentialStatus(null),
      };
  }
  candidate.type = String(descriptor?.id || "");
  const presets = (descriptor?.preset_definitions || descriptor?.presets || [])
    .filter((preset) => (
      typeof preset === "string"
      || !preset.capabilities?.length
      || preset.capabilities.includes(kind)
    ))
    .map((preset) => (typeof preset === "string" ? preset : preset.id));
  if (!presets.includes(candidate.preset)) candidate.preset = presets[0] || "";
  candidate.probe = null;
  next.touched[touchedKey(kind, id, "type")] = true;
  return { state: markChanged(next), incompatibleFields, changed: true };
}

function fieldFromPath(path) {
  if (String(path).includes(".credentials.") || String(path).includes("models.credentials")) {
    return "credential";
  }
  const parts = String(path).replaceAll("[", ".").replaceAll("]", "").split(".").filter(Boolean);
  return parts.at(-1) || "configuration";
}

export function mapServerFieldErrors(state, errors) {
  const next = clone(state);
  next.fieldErrors = emptyFieldErrors();
  for (const raw of Array.isArray(errors) ? errors : []) {
    const error = {
      path: String(raw?.path || ""),
      code: String(raw?.code || "validation_failed"),
      message: String(raw?.message || "Invalid model configuration."),
      source: String(raw?.source || ""),
    };
    const id = raw?.connection_id ? String(raw.connection_id) : "";
    if (!id) {
      next.fieldErrors.global.push(error);
      continue;
    }
    const field = fieldFromPath(error.path);
    const fields = ownFieldErrorBucket(next.fieldErrors.byConnection, id);
    Object.defineProperty(fields, field, {
      value: error,
      configurable: true,
      enumerable: true,
      writable: true,
    });
  }
  return next;
}

export function receiveRemoteSnapshot(state, snapshot) {
  const remoteRevision = String(snapshot?.revision || "");
  if (!remoteRevision || remoteRevision === state.revision) return clone(state);
  if (!state.dirty) {
    const hydrated = hydrateModelConfig(snapshot);
    for (const kind of ["chat", "embedding"]) {
      const selectedId = state.selected?.[kind];
      if (selectedId && routeItems(hydrated, kind).some((item) => item.id === selectedId)) {
        hydrated.selected[kind] = selectedId;
      }
    }
    if (state.activeRoute) hydrated.activeRoute = state.activeRoute;
    return hydrated;
  }
  const next = clone(state);
  next.remoteUpdate = {
    latestRevision: remoteRevision,
    snapshot: clone(snapshot),
  };
  return next;
}

export function setMigrationResolution(state, issueId, resolution) {
  const next = clone(state);
  const selected = clone(resolution);
  delete selected.position;
  next.migration_resolutions[String(issueId)] = selected;
  return markChanged(next);
}

function credentialPayload(credential) {
  const action = ["keep", "set", "clear", "env"].includes(credential?.action)
    ? credential.action
    : "keep";
  if (action === "set" || action === "env") {
    return { action, value: String(credential?.value || "") };
  }
  return { action };
}

function chatPayload(item) {
  return {
    id: String(item.id || ""),
    name: String(item.name || ""),
    type: String(item.type || ""),
    model: String(item.model || ""),
    preset: String(item.preset || ""),
    base_url: String(item.base_url || ""),
    credential: credentialPayload(item.credential),
    api_mode: String(item.api_mode || ""),
    reasoning_effort: String(item.reasoning_effort || ""),
    http_referer: String(item.http_referer || ""),
    x_title: String(item.x_title || ""),
    num_ctx: Math.max(0, Number(item.num_ctx) || 0),
  };
}

function embeddingPayload(item) {
  return {
    id: String(item.id || ""),
    name: String(item.name || ""),
    type: String(item.type || ""),
    preset: String(item.preset || ""),
    base_url: String(item.base_url || ""),
    credential: credentialPayload(item.credential),
  };
}

function embeddingSettingsPayload(settings) {
  return {
    model: String(settings?.model || ""),
    output_dimensionality: Math.max(
      0,
      Number(settings?.output_dimensionality) || 0,
    ),
    similarity_threshold: Number(settings?.similarity_threshold),
    multimodal_enabled: Boolean(settings?.multimodal_enabled),
  };
}

export function createProbeSignature(state, kind, id) {
  const index = findIndex(state, kind, id);
  const item = routeItems(state, kind)[index];
  const draft = kind === "chat"
    ? chatPayload(item)
    : {
      provider: embeddingPayload(item),
      settings: embeddingSettingsPayload(state.models.embedding.settings),
    };
  return {
    revision: String(state.revision || ""),
    kind,
    id: String(id),
    fingerprint: JSON.stringify(draft),
  };
}

export function probeSignatureMatches(state, signature) {
  if (!signature || typeof signature !== "object") return false;
  try {
    return valuesEqual(
      createProbeSignature(state, signature.kind, signature.id),
      signature,
    );
  } catch (_error) {
    return false;
  }
}

export function applyProbeResult(state, signature, result) {
  const next = clone(state);
  if (!probeSignatureMatches(next, signature)) {
    return { state: next, accepted: false };
  }
  const index = findIndex(next, signature.kind, signature.id);
  const stored = clone(result);
  // Persist the exact-draft fingerprint alongside the probe so the settings UI
  // can detect "changed since last verified" (decision 4) even if a later
  // mutation path forgot to clear the probe chip.
  if (stored && typeof stored === "object" && !stored.fingerprint) {
    stored.fingerprint = signature.fingerprint;
  }
  routeItems(next, signature.kind)[index].probe = stored;
  return { state: next, accepted: true };
}

export function toModelConfigPayload(state) {
  // The backend rejects enabled=false WITH providers outright
  // (embedding_disabled_with_providers). Route items stay in client state so a
  // same-session re-enable restores them, but the wire payload must not carry
  // providers on a disabled route.
  const embeddingEnabled = Boolean(state.models.embedding.enabled);
  return {
    revision: state.revision,
    models: {
      schema_version: 1,
      chat: {
        connections: state.models.chat.connections.map(chatPayload),
        concurrency: Math.max(1, Number(state.models.chat.concurrency) || 1),
        timeout_seconds: Math.max(10, Number(state.models.chat.timeout_seconds) || 10),
      },
      embedding: {
        enabled: embeddingEnabled,
        settings: embeddingSettingsPayload(state.models.embedding.settings),
        providers: embeddingEnabled
          ? state.models.embedding.providers.map(embeddingPayload)
          : [],
      },
    },
    migration_resolutions: clone(state.migration_resolutions || {}),
  };
}

export function selectedRecord(state, kind) {
  if (kind !== "chat" && kind !== "embedding") return null;
  return routeItems(state, kind).find((item) => item.id === state.selected[kind]) || null;
}

/* ── Circuit-breaker view helpers (decision 6) ─────────────────────── */

/**
 * Project a record's circuit summary into a display model. Returns null for a
 * closed/unknown circuit so callers can render nothing. `nowMs` is injectable
 * for deterministic tests and countdown re-renders.
 */
export function circuitView(record, nowMs = Date.now()) {
  const circuit = record?.circuit;
  if (!circuit || circuit.state !== "open") return null;
  const retryAfter = Number(circuit.retry_after_seconds);
  const hasRetry = Number.isFinite(retryAfter) && retryAfter > 0;
  const retrySeconds = hasRetry ? Math.max(0, Math.ceil(retryAfter)) : null;
  return {
    state: "open",
    failureKind: String(circuit.failure_kind || ""),
    permanent: Boolean(circuit.permanent),
    retrySeconds,
    label: circuit.permanent
      ? `熔断打开 · ${circuit.failure_kind || "永久失败"}`
      : retrySeconds !== null
        ? `熔断打开 · ${retrySeconds}s 后重试`
        : `熔断打开${circuit.failure_kind ? ` · ${circuit.failure_kind}` : ""}`,
  };
}

/** Throttled aria-live announcement key: only changes on state transitions. */
export function circuitAnnouncementKey(record) {
  const circuit = record?.circuit;
  if (!circuit || circuit.state !== "open") return "closed";
  return `open:${circuit.failure_kind || ""}:${circuit.permanent ? "1" : "0"}`;
}

/* ── Changed-since-probe detection (decision 4) ────────────────────── */

/**
 * True when the record carries a successful probe whose exact-draft
 * fingerprint no longer matches the current draft. Records never probed (or
 * with a failed probe) return false — the settings UI only warns about
 * *unverified changes* on top of a previously verified draft.
 */
export function hasUnverifiedChanges(state, kind, id) {
  let index;
  try {
    index = findIndex(state, kind, id);
  } catch (_error) {
    return false;
  }
  const item = routeItems(state, kind)[index];
  if (!item?.probe?.ok) return false;
  // updateRouteField clears probe on fingerprint changes, so a surviving
  // probe is by construction in sync — unless the caller re-attached an old
  // probe result. Compare fingerprints defensively via the stored signature.
  if (!item.probe?.fingerprint) return false;
  try {
    const current = createProbeSignature(state, kind, id);
    return current.fingerprint !== item.probe.fingerprint;
  } catch (_error) {
    return false;
  }
}

/** Collect every route item with unverified changes for a save-time warning. */
export function unverifiedConnections(state) {
  const changed = [];
  for (const kind of ["chat", "embedding"]) {
    for (const item of routeItems(state, kind)) {
      if (hasUnverifiedChanges(state, kind, item.id)) {
        changed.push({ kind, id: item.id, name: item.name || item.id });
      }
    }
  }
  return changed;
}

/* ── Override-lock selectors (decision 7) ──────────────────────────── */

/** Lock descriptor ({path, source}) for a control path, or null when editable. */
export function overrideLockFor(state, path) {
  return state?.overrideLocks?.[path] || null;
}

/** True when any model control is locked by a higher-priority config source. */
export function hasOverrideLocks(state) {
  return Object.values(state?.overrideLocks || {}).some(Boolean);
}

/* ── Single-connection draft mode for the setup wizard ─────────────── */

/**
 * Wrap the full route state in a single-connection editing lens used by the
 * setup wizard. The wizard edits exactly one chat connection (plus, in the
 * embedding step, one provider) while preserving every other route item
 * verbatim in the serialized PUT payload.
 */
export function createSingleConnectionDraft(state, kind, id) {
  const index = findIndex(state, kind, id);
  const record = routeItems(state, kind)[index];
  return {
    get record() {
      return record;
    },
    get kind() {
      return kind;
    },
    get id() {
      return record.id;
    },
    /** Count of route items preserved untouched after the edited one. */
    get preservedFallbackCount() {
      return Math.max(0, routeItems(state, kind).length - 1);
    },
    updateField(field, value) {
      return updateRouteField(state, kind, record.id, field, value);
    },
    applyPreset(presetDefinition, options) {
      return applyPreset(state, kind, record.id, presetDefinition, options);
    },
    changeType(descriptor, options) {
      return changeConnectionType(state, kind, record.id, descriptor, options);
    },
    probeSignature() {
      return createProbeSignature(state, kind, record.id);
    },
    toPayload(nextState = state) {
      return toModelConfigPayload(nextState);
    },
  };
}

/**
 * Build the guarded one-click local Embedding draft used by the popup banner.
 *
 * This convenience action only creates an empty route or refreshes one existing
 * Ollama provider.  It refuses to replace a configured provider list so no
 * credential-bearing record can disappear without the user editing the model
 * route explicitly.
 */
export function prepareLocalOllamaEmbedding(state, descriptor, defaults) {
  if (state?.dirty) {
    throw new Error("Unsaved model changes must be saved or reloaded first.");
  }
  if (
    descriptor?.id !== "ollama"
    || !descriptor?.capabilities?.includes("embedding")
  ) {
    throw new Error("The server does not advertise an Embedding-capable Ollama descriptor.");
  }
  const baseUrlField = (descriptor.fields || []).find((field) => (
    field.name === "base_url"
    && (!field.capabilities?.length || field.capabilities.includes("embedding"))
  ));
  if (!baseUrlField) {
    throw new Error("The Ollama descriptor does not expose an Embedding endpoint field.");
  }
  for (const path of [
    "models.embedding.providers",
    "models.embedding.enabled",
    "models.embedding.settings.model",
    "models.embedding.settings.output_dimensionality",
    "models.embedding.settings.multimodal_enabled",
  ]) {
    if (state?.overrideLocks?.[path]) {
      throw new Error(`A read-only override controls ${path}.`);
    }
  }

  const providers = state?.models?.embedding?.providers || [];
  if (providers.length > 1 || providers.some((provider) => provider.type !== "ollama")) {
    throw new Error(
      "A configured Embedding route must be edited explicitly before enabling local Ollama.",
    );
  }

  const next = clone(state);
  const existing = next.models.embedding.providers[0] || null;
  const id = String(existing?.id || defaults?.id || "embedding-local-ollama");
  const record = cleanDraftRecord({
    id,
    name: String(existing?.name || defaults?.name || descriptor.label || "Local Ollama"),
    type: "ollama",
    preset: "",
    base_url: String(existing?.base_url || defaults?.base_url || ""),
    credential: { action: "clear", value: "" },
  }, "embedding");
  next.models.embedding.providers = [record];
  next.models.embedding.enabled = true;
  next.models.embedding.settings.model = String(defaults?.model || "");
  next.models.embedding.settings.output_dimensionality = Math.max(
    0,
    Number(defaults?.output_dimensionality) || 0,
  );
  next.models.embedding.settings.multimodal_enabled = false;
  next.selected.embedding = id;
  return markChanged(next);
}

export { MAX_ROUTE_ITEMS };
