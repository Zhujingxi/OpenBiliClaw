/**
 * DOM-free state transitions for every web model-route editor.
 *
 * Public snapshots contain credential status only.  Hydration deliberately
 * replaces that status with an explicit write action and keeps the safe status
 * beside it, so a masked or raw persisted secret can never enter a draft.
 */

export type RouteKind = "chat" | "embedding";

export interface CredentialStatus {
  source: string;
  configured: boolean;
  env_name: string;
  credential_ref: string;
  oauth_logged_in: boolean;
}

export interface CredentialDraft {
  action: string;
  value: string;
  status: CredentialStatus;
  [key: string]: unknown;
}

export interface CircuitSummary {
  state: string;
  failure_kind?: string;
  permanent?: boolean;
  retry_after_seconds?: number;
  [key: string]: unknown;
}

export interface ProbeResult {
  ok?: boolean;
  fingerprint?: string;
  [key: string]: unknown;
}

export interface RouteRecord {
  id: string;
  name: string;
  type: string;
  preset: string;
  base_url: string;
  credential: CredentialDraft;
  probe: ProbeResult | null;
  circuit: CircuitSummary;
  model?: string;
  api_mode?: string;
  reasoning_effort?: string;
  http_referer?: string;
  x_title?: string;
  num_ctx?: number;
  [key: string]: unknown;
}

export interface FieldError {
  path: string;
  code: string;
  message: string;
  source: string;
}

export interface FieldErrors {
  byConnection: Record<string, Record<string, FieldError>>;
  global: FieldError[];
}

export interface OverrideEntry {
  path: string;
  source: string;
  [key: string]: unknown;
}

export interface EmbeddingSettings {
  model: string;
  output_dimensionality: number;
  similarity_threshold: number;
  multimodal_enabled: boolean;
  [key: string]: unknown;
}

export interface ModelConfigState {
  revision: string;
  source: string;
  models: {
    schema_version: number;
    chat: {
      connections: RouteRecord[];
      concurrency: number;
      timeout_seconds: number;
      [key: string]: unknown;
    };
    embedding: {
      enabled: boolean;
      settings: EmbeddingSettings;
      providers: RouteRecord[];
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
  migration: unknown;
  overrides: OverrideEntry[];
  overrideLocks: Record<string, OverrideEntry | null>;
  selected: { chat: string; embedding: string; [key: string]: string };
  touched: Record<string, boolean>;
  dirty: boolean;
  remoteUpdate: { latestRevision: string; snapshot: unknown } | null;
  fieldErrors: FieldErrors;
  migration_resolutions: Record<string, unknown>;
  activeRoute?: string;
  [key: string]: unknown;
}

export interface RequestGate {
  begin(): number;
  invalidate(): void;
  isCurrent(candidate: number): boolean;
}

interface LatestRequestOptions<T> {
  gate: RequestGate;
  generation: number;
  request: () => Promise<T>;
  blocked: () => boolean;
  apply: (value: T) => void;
  onBlocked?: ((value: T) => void) | null;
}

export interface SnapshotRequestOptions<T> {
  gate: RequestGate;
  request: () => Promise<T>;
  blocked: () => boolean;
  apply: (value: T) => void;
  onBlocked?: ((value: T) => void) | null;
}

export interface IndependentResourcesOptions<S, D> {
  gate: RequestGate;
  descriptorGate?: RequestGate;
  snapshotRequest: () => Promise<S>;
  descriptorRequest: () => Promise<D>;
  blocked: () => boolean;
  onSnapshotBlocked?: ((value: S) => void) | null;
  applySnapshot: (value: S) => void;
  installDescriptors: (value: D) => void;
}

export interface DescriptorField {
  name: string;
  capabilities?: string[];
  presets?: string[];
  [key: string]: unknown;
}

export interface PresetDefinition {
  id: string;
  defaults?: Record<string, unknown>;
  capabilities?: string[];
  [key: string]: unknown;
}

export interface ConnectionDescriptor {
  id?: string;
  label?: string;
  category?: string;
  capabilities?: string[];
  fields?: DescriptorField[];
  preset_definitions?: PresetDefinition[];
  presets?: Array<string | PresetDefinition>;
  [key: string]: unknown;
}

export interface ProbeSignature {
  revision: string;
  kind: string;
  id: string;
  fingerprint: string;
}

export interface ModelOperationGate {
  readonly saveGeneration: number;
  readonly saveInFlight: boolean;
  readonly probeInFlight: boolean;
  beginProbe(): number;
  isProbeCurrent(candidate: number): boolean;
  finishProbe(candidate: number): boolean;
  beginSave(): { generation: number; invalidatedProbe: boolean } | null;
  finishSave(candidate: number): boolean;
  canStartSaveAfterLoad(args: {
    startedSaveGeneration: number;
    loadResult: { snapshotApplied?: boolean; descriptorsInstalled?: boolean } | null;
    state: ModelConfigState | null;
  }): boolean;
  controlState(): { editorLocked: boolean; saveDisabled: boolean; probeDisabled: boolean };
}

// TODO(types): backend model-config snapshots are opaque at this browser
// boundary; narrow only the wire fields read during hydration.
/** Raw server snapshot shape (loosely typed; fields normalized on hydrate). */
export type RawSnapshot = {
  revision?: unknown;
  source?: unknown;
  models?: unknown;
  overrides?: unknown;
  migration?: unknown;
  [key: string]: unknown;
} | null | undefined;

/** Raw route record from the wire (every field unknown until hydrated). */
export type RawRecord = Record<string, unknown> | null | undefined;

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

function clone<T>(value: T): T {
  if (typeof structuredClone === "function") return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

function valuesEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function createLatestRequestGate(): RequestGate {
  let generation = 0;
  return {
    begin() {
      generation += 1;
      return generation;
    },
    invalidate() {
      generation += 1;
    },
    isCurrent(candidate: number) {
      return candidate === generation;
    },
  };
}

async function applyLatestRequestGeneration<T>({
  gate,
  generation,
  request,
  blocked,
  apply,
  onBlocked = null,
}: LatestRequestOptions<T>): Promise<boolean> {
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

export async function applyLatestSnapshotRequest<T>(options: SnapshotRequestOptions<T>): Promise<boolean> {
  const generation = options.gate.begin();
  return applyLatestRequestGeneration({ ...options, generation });
}

/** Coordinate one revisioned save with exact-probe request ownership. */
export function createModelOperationGate(): ModelOperationGate {
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
    isProbeCurrent(candidate: number) {
      return candidate === probeGeneration;
    },
    finishProbe(candidate: number) {
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
    finishSave(candidate: number) {
      if (!saveInFlight || candidate !== saveGeneration) return false;
      saveInFlight = false;
      return true;
    },
    canStartSaveAfterLoad({ startedSaveGeneration, loadResult, state }: { startedSaveGeneration: number; loadResult: { snapshotApplied?: boolean; descriptorsInstalled?: boolean } | null; state: ModelConfigState | null }) {
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
export async function loadIndependentModelResources<S, D>({
  gate,
  descriptorGate = createLatestRequestGate(),
  snapshotRequest,
  descriptorRequest,
  blocked,
  onSnapshotBlocked = null,
  applySnapshot,
  installDescriptors,
}: IndependentResourcesOptions<S, D>): Promise<{ snapshotApplied: boolean; descriptorsInstalled: boolean }> {
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

function normalizeOverridePath(path: unknown): string {
  return String(path || "")
    .replace(/\[(\d+)\]/g, ".$1")
    .replace(/^\.+|\.+$/g, "");
}

function buildOverrideLocks(overrides: unknown): Record<string, OverrideEntry | null> {
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

function routeKey(kind: RouteKind): "connections" | "providers" {
  if (kind === "chat") return "connections";
  if (kind === "embedding") return "providers";
  throw new Error(`Unknown model route: ${kind}`);
}

function routeItems(state: ModelConfigState, kind: RouteKind): RouteRecord[] {
  return state.models[kind][routeKey(kind)] as RouteRecord[];
}

function safeCredentialStatus(raw: RawRecord): CredentialStatus {
  const source = ["none", "inline", "env", "oauth"].includes(raw?.source as string)
    ? raw!.source as string
    : "none";
  return {
    source,
    configured: Boolean(raw?.configured),
    env_name: typeof raw?.env_name === "string" ? raw.env_name : "",
    credential_ref: typeof raw?.credential_ref === "string" ? raw.credential_ref : "",
    oauth_logged_in: Boolean(raw?.oauth_logged_in),
  };
}

function hydrateCredential(raw: RawRecord): CredentialDraft {
  return {
    action: "keep",
    value: "",
    status: safeCredentialStatus(raw),
  };
}

function hydrateRecord(raw: RawRecord, kind: RouteKind): RouteRecord {
  const record: RouteRecord = {
    id: String(raw?.id || ""),
    name: String(raw?.name || ""),
    type: String(raw?.type || ""),
    preset: String(raw?.preset || ""),
    base_url: String(raw?.base_url || ""),
    credential: hydrateCredential(raw?.credential as RawRecord),
    probe: raw?.probe ? clone(raw.probe) as ProbeResult : null,
    circuit: raw?.circuit ? clone(raw.circuit) as CircuitSummary : { state: "closed" },
  };
  if (kind === "chat") {
    Object.assign(record, {
      model: String(raw?.model || ""),
      api_mode: String(raw?.api_mode || ""),
      reasoning_effort: String(raw?.reasoning_effort || ""),
      http_referer: String(raw?.http_referer || ""),
      x_title: String(raw?.x_title || ""),
      num_ctx: Number.isFinite(Number(raw?.num_ctx)) ? Number(raw!.num_ctx) : 0,
    });
  }
  return record;
}

function emptyFieldErrors(): FieldErrors {
  return { byConnection: {}, global: [] };
}

function ownFieldErrorBucket(container: Record<string, Record<string, FieldError>>, key: string): Record<string, FieldError> {
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

function touchedKey(kind: RouteKind, id: string, field: string): string {
  return `${kind}:${id}:${field}`;
}

function markChanged<T extends ModelConfigState>(state: T): T {
  state.dirty = true;
  state.fieldErrors = emptyFieldErrors();
  return state;
}

function findIndex(state: ModelConfigState, kind: RouteKind, id: string): number {
  const index = routeItems(state, kind).findIndex((item) => item.id === id);
  if (index < 0) throw new Error(`Unknown ${kind} connection ID: ${id}`);
  return index;
}

function cleanDraftRecord(record: RawRecord, kind: RouteKind): RouteRecord {
  const hydrated = hydrateRecord(record, kind);
  const action = (record?.credential as RawRecord)?.action;
  if (["keep", "set", "clear", "env"].includes(action as string)) {
    hydrated.credential.action = action as string;
    hydrated.credential.value = ["set", "env"].includes(action as string)
      ? String((record!.credential as RawRecord)!.value || "")
      : "";
  } else if (!(record?.credential as RawRecord)?.source && !(record?.credential as RawRecord)?.status) {
    hydrated.credential.action = "clear";
  }
  return hydrated;
}

export function hydrateModelConfig(snapshot: RawSnapshot): ModelConfigState {
  const source = snapshot && typeof snapshot === "object" ? snapshot : {};
  const models = (source.models && typeof source.models === "object" ? source.models : {}) as Record<string, unknown>;
  const chat = (models.chat && typeof models.chat === "object" ? models.chat : {}) as Record<string, unknown>;
  const embedding = (models.embedding && typeof models.embedding === "object"
    ? models.embedding
    : {}) as Record<string, unknown>;

  const connections = Array.isArray(chat.connections)
    ? chat.connections.map((item: unknown) => hydrateRecord(item as RawRecord, "chat"))
    : [];
  const providers = Array.isArray(embedding.providers)
    ? embedding.providers.map((item: unknown) => hydrateRecord(item as RawRecord, "embedding"))
    : [];
  const overrides = clone(Array.isArray(source.overrides) ? source.overrides : []) as OverrideEntry[];
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
          model: String((embedding.settings as RawRecord)?.model || ""),
          output_dimensionality: Math.max(
            0,
            Number((embedding.settings as RawRecord)?.output_dimensionality) || 0,
          ),
          similarity_threshold: Number.isFinite(Number((embedding.settings as RawRecord)?.similarity_threshold))
            ? Number((embedding.settings as RawRecord)!.similarity_threshold)
            : 0.82,
          multimodal_enabled: Boolean((embedding.settings as RawRecord)?.multimodal_enabled),
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

export function selectRouteItem(state: ModelConfigState, kind: RouteKind, id: string): ModelConfigState {
  findIndex(state, kind, id);
  const next = clone(state);
  next.selected[kind] = id;
  return next;
}

export function appendRouteItem(state: ModelConfigState, kind: RouteKind, record: RawRecord): ModelConfigState {
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

export function removeRouteItem(state: ModelConfigState, kind: RouteKind, id: string): ModelConfigState {
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

export function moveRouteItem(state: ModelConfigState, kind: RouteKind, id: string, targetIndex: number): ModelConfigState {
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

export function updateRouteField(state: ModelConfigState, kind: RouteKind, id: string, field: string, value: unknown): ModelConfigState {
  const next = clone(state);
  const index = findIndex(next, kind, id);
  const item = (next.models[kind][routeKey(kind)] as RouteRecord[])[index];
  if (field === "id") throw new Error("Stable connection IDs cannot be edited.");
  const previousValue = item[field] === undefined ? undefined : clone(item[field]);
  if (field === "credential") {
    const action = String((value as RawRecord)?.action || "keep");
    if (!["keep", "set", "clear", "env"].includes(action)) {
      throw new Error(`Unknown credential action: ${action}`);
    }
    item.credential = {
      ...item.credential,
      action,
      value: ["set", "env"].includes(action) ? String((value as RawRecord)?.value || "") : "",
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

export function updateRouteSetting(state: ModelConfigState, kind: RouteKind, field: string, value: unknown): ModelConfigState {
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

export function applyPreset(state: ModelConfigState, kind: RouteKind, id: string, presetDefinition: PresetDefinition | null | undefined, options: { previousPreset?: PresetDefinition | null } = {}): ModelConfigState {
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
  state: ModelConfigState,
  kind: RouteKind,
  id: string,
  descriptor: ConnectionDescriptor | null | undefined,
  presetDefinition: PresetDefinition | null | undefined,
  options: { confirmed?: boolean } = {},
): { state: ModelConfigState; incompatibleFields: string[]; changed: boolean } {
  const index = findIndex(state, kind, id);
  const item = routeItems(state, kind)[index];
  const nextPreset = String(presetDefinition?.id || "");
  const allowed = new Set(
    (descriptor?.fields || [])
      .filter((field: DescriptorField) => (
        (!field.capabilities?.length || field.capabilities.includes(kind))
        && (!field.presets?.length || field.presets.includes(nextPreset))
      ))
      .map((field: DescriptorField) => field.name),
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

function isPopulated(field: string, value: unknown): boolean {
  if (field === "credential") {
    const status = (value as CredentialDraft | null | undefined)?.status;
    return Boolean(
      (value as CredentialDraft | null | undefined)?.action === "set"
      || (value as CredentialDraft | null | undefined)?.action === "env"
      || status?.configured
      || status?.source === "oauth",
    );
  }
  if (typeof value === "string") return Boolean(value.trim());
  if (typeof value === "number") return value !== 0;
  return Boolean(value);
}

function clearField(item: RouteRecord, field: string): void {
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

export function changeConnectionType(
  state: ModelConfigState,
  kind: RouteKind,
  id: string,
  descriptor: ConnectionDescriptor | null | undefined,
  options: { confirmed?: boolean; previousDescriptor?: ConnectionDescriptor | null } = {},
): { state: ModelConfigState; incompatibleFields: string[]; changed: boolean } {
  const index = findIndex(state, kind, id);
  const item = routeItems(state, kind)[index];
  const previousCategory = String(options.previousDescriptor?.category || "");
  const nextCategory = String(descriptor?.category || "");
  const credentialSemanticsChanged = Boolean(previousCategory)
    && ((previousCategory === "oauth") !== (nextCategory === "oauth"));
  const allowed = new Set(
    (descriptor?.fields || [])
      .filter((field: DescriptorField) => (
        (!field.capabilities?.length || field.capabilities.includes(kind))
        && (!field.presets?.length || field.presets.includes(item.preset))
      ))
      .map((field: DescriptorField) => field.name),
  );
  const possible = kind === "chat" ? CHAT_FIELDS : EMBEDDING_FIELDS;
  const incompatibleFields = possible.filter(
    (field: string) => !allowed.has(field) && isPopulated(field, item[field]),
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
    .filter((preset: string | PresetDefinition) => (
      typeof preset === "string"
      || !preset.capabilities?.length
      || preset.capabilities.includes(kind)
    ))
    .map((preset: string | PresetDefinition) => (typeof preset === "string" ? preset : preset.id));
  if (!presets.includes(candidate.preset)) candidate.preset = presets[0] || "";
  candidate.probe = null;
  next.touched[touchedKey(kind, id, "type")] = true;
  return { state: markChanged(next), incompatibleFields, changed: true };
}

function fieldFromPath(path: unknown): string {
  if (String(path).includes(".credentials.") || String(path).includes("models.credentials")) {
    return "credential";
  }
  const parts = String(path).replaceAll("[", ".").replaceAll("]", "").split(".").filter(Boolean);
  return parts.at(-1) || "configuration";
}

export function mapServerFieldErrors(state: ModelConfigState, errors: unknown): ModelConfigState {
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

export function receiveRemoteSnapshot(state: ModelConfigState, snapshot: RawSnapshot): ModelConfigState {
  const remoteRevision = String(snapshot?.revision || "");
  if (!remoteRevision || remoteRevision === state.revision) return clone(state);
  if (!state.dirty) {
    const hydrated = hydrateModelConfig(snapshot);
    for (const kind of ["chat", "embedding"] as RouteKind[]) {
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

export function setMigrationResolution(state: ModelConfigState, issueId: unknown, resolution: Record<string, unknown>): ModelConfigState {
  const next = clone(state);
  const selected = clone(resolution);
  delete selected.position;
  next.migration_resolutions[String(issueId)] = selected;
  return markChanged(next);
}

function credentialPayload(credential: RawRecord): Record<string, unknown> {
  const action = ["keep", "set", "clear", "env"].includes(credential?.action as string)
    ? credential!.action as string
    : "keep";
  if (action === "set" || action === "env") {
    return { action, value: String(credential?.value || "") };
  }
  return { action };
}

function chatPayload(item: RouteRecord): Record<string, unknown> {
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

function embeddingPayload(item: RouteRecord): Record<string, unknown> {
  return {
    id: String(item.id || ""),
    name: String(item.name || ""),
    type: String(item.type || ""),
    preset: String(item.preset || ""),
    base_url: String(item.base_url || ""),
    credential: credentialPayload(item.credential),
  };
}

function embeddingSettingsPayload(settings: RawRecord): Record<string, unknown> {
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

export function createProbeSignature(state: ModelConfigState, kind: RouteKind, id: string): ProbeSignature {
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

export function probeSignatureMatches(state: ModelConfigState, signature: ProbeSignature | null | undefined): boolean {
  if (!signature || typeof signature !== "object") return false;
  try {
    return valuesEqual(
      createProbeSignature(state, signature.kind as RouteKind, signature.id),
      signature,
    );
  } catch (_error) {
    return false;
  }
}

export function applyProbeResult(state: ModelConfigState, signature: ProbeSignature, result: unknown): { state: ModelConfigState; accepted: boolean } {
  const next = clone(state);
  if (!probeSignatureMatches(next, signature)) {
    return { state: next, accepted: false };
  }
  const index = findIndex(next, signature.kind as RouteKind, signature.id);
  const stored = clone(result) as ProbeResult | null;
  // Persist the exact-draft fingerprint alongside the probe so the settings UI
  // can detect "changed since last verified" (decision 4) even if a later
  // mutation path forgot to clear the probe chip.
  if (stored && typeof stored === "object" && !stored.fingerprint) {
    stored.fingerprint = signature.fingerprint;
  }
  routeItems(next, signature.kind as RouteKind)[index].probe = stored;
  return { state: next, accepted: true };
}

export function toModelConfigPayload(state: ModelConfigState): Record<string, unknown> {
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

export function selectedRecord(state: ModelConfigState, kind: RouteKind): RouteRecord | null {
  if (kind !== "chat" && kind !== "embedding") return null;
  return routeItems(state, kind).find((item) => item.id === state.selected[kind]) || null;
}

/* ── Circuit-breaker view helpers (decision 6) ─────────────────────── */

/**
 * Project a record's circuit summary into a display model. Returns null for a
 * closed/unknown circuit so callers can render nothing. `nowMs` is injectable
 * for deterministic tests and countdown re-renders.
 */
export function circuitView(record: RouteRecord | null | undefined, nowMs: number = Date.now()): { state: string; failureKind: string; permanent: boolean; retrySeconds: number | null; label: string } | null {
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
export function circuitAnnouncementKey(record: RouteRecord | null | undefined): string {
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
export function hasUnverifiedChanges(state: ModelConfigState, kind: RouteKind, id: string): boolean {
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
export function unverifiedConnections(state: ModelConfigState): Array<{ kind: RouteKind; id: string; name: string }> {
  const changed = [];
  for (const kind of ["chat", "embedding"] as RouteKind[]) {
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
export function overrideLockFor(state: ModelConfigState | null | undefined, path: string): OverrideEntry | null {
  return state?.overrideLocks?.[path] || null;
}

/** True when any model control is locked by a higher-priority config source. */
export function hasOverrideLocks(state: ModelConfigState | null | undefined): boolean {
  return Object.values(state?.overrideLocks || {}).some(Boolean);
}

/* ── Single-connection draft mode for the setup wizard ─────────────── */

/**
 * Wrap the full route state in a single-connection editing lens used by the
 * setup wizard. The wizard edits exactly one chat connection (plus, in the
 * embedding step, one provider) while preserving every other route item
 * verbatim in the serialized PUT payload.
 */
export function createSingleConnectionDraft(state: ModelConfigState, kind: RouteKind, id: string) {
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
    updateField(field: string, value: unknown) {
      return updateRouteField(state, kind, record.id, field, value);
    },
    applyPreset(presetDefinition: PresetDefinition | null | undefined, options?: { previousPreset?: PresetDefinition | null }) {
      return applyPreset(state, kind, record.id, presetDefinition, options);
    },
    changeType(descriptor: ConnectionDescriptor | null | undefined, options?: { confirmed?: boolean; previousDescriptor?: ConnectionDescriptor | null }) {
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
export function prepareLocalOllamaEmbedding(state: ModelConfigState, descriptor: ConnectionDescriptor | null | undefined, defaults: Record<string, unknown> | null | undefined): ModelConfigState {
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
