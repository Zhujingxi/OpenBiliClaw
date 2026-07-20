/**
 * Ambient module declarations for Phase-B shared web modules, which are still
 * plain `.js` on the Phase C branch. These give the mobile `.ts` sources typed
 * signatures for the exact imports they use; Phase B replaces this file with
 * real `.ts` sources (this shim should be deleted then).
 *
 * TODO(types): align shapes with the real implementations when Phase B lands.
 */

// ── saved-sync-core.js ──────────────────────────────────────

/** Saved item payload (opaque backend rows; unknown field values). */
interface ObscSavedItemInput {
  source_platform?: unknown;
  platform?: unknown;
  item_key?: unknown;
  content_id?: unknown;
  bvid?: unknown;
  content_url?: unknown;
  url?: unknown;
  content_type?: unknown;
  title?: unknown;
  author_name?: unknown;
  up_name?: unknown;
  author?: unknown;
  cover_url?: unknown;
  note?: unknown;
  [key: string]: unknown;
}

interface ObscSavedSyncTaskItem {
  item_key?: string;
  status?: string;
  [key: string]: unknown;
}

interface ObscSavedSyncTask {
  task_id?: string;
  items?: ObscSavedSyncTaskItem[];
  [key: string]: unknown;
}

interface ObscSavedListSnapshot {
  items: ObscSavedItemInput[];
  total: number;
  loaded: boolean;
  error: string;
}

interface ObscSavedFocusToken {
  kind?: string;
  action?: string;
  itemKey?: string;
  index?: number;
}

interface ObscTaskTrackerCallbacks {
  onTerminal?: (task: ObscSavedSyncTask) => void;
  onProgress?: (task: ObscSavedSyncTask) => void;
  onBackground?: (task: ObscSavedSyncTask) => void;
  onPollError?: (error: unknown, task?: ObscSavedSyncTask) => void;
}

interface ObscDurableTaskTracker {
  has(taskId: unknown): boolean;
  track(initial: unknown, callbacks?: ObscTaskTrackerCallbacks): string | null;
  resume(taskId: unknown): boolean;
  stop(taskId: unknown): boolean;
  resumeAll(): number;
  dispose(): void;
}

interface ObscSavedTaskCoordinator {
  owns(itemKey: unknown): boolean;
  taskFor(itemKey: unknown): string;
  track(
    task: unknown,
    values: unknown,
    callbacks?: ObscTaskTrackerCallbacks,
  ): string | null;
  recover(rows: unknown, callbacks?: ObscTaskTrackerCallbacks): Promise<void>;
  resumeAll(): number;
  dispose(): void;
}

interface ObscSavedMutationOperations {
  add(itemKey: string): Promise<unknown>;
  remove(itemKey: string): Promise<unknown>;
}

interface ObscSavedMutationRegistry {
  isBusy(listKind: string, itemKey: unknown): boolean;
  isSaved(listKind: string, itemKey: unknown): boolean;
  setSaved(listKind: string, itemKey: unknown, value: unknown): void;
  hydrate(
    listKind: string,
    itemKey: unknown,
    load: (itemKey: unknown) => Promise<unknown>,
  ): Promise<unknown>;
  toggle(
    listKind: string,
    itemKey: unknown,
    operations: ObscSavedMutationOperations,
  ): Promise<boolean>;
}

interface ObscSavedSubmissionFence {
  has(itemKey: unknown): boolean;
  claim(values: unknown): boolean;
  release(values: unknown): void;
}

interface ObscRetainedSavedListState {
  commit(payload?: { items?: unknown; total?: unknown }): void;
  fail(reason: unknown): void;
  snapshot(): ObscSavedListSnapshot;
}

interface ObscDialogFocusControllerOptions {
  dialog?: unknown;
  opener?: unknown;
  document?: Document | null;
  resolveOpener?: () => { focus?: () => void } | null;
  onClose?: () => void;
}

interface ObscDialogFocusController {
  activate(): void;
  deactivate(): void;
}

interface ObscDurableTaskTrackerOptions {
  poll?: (taskId: string) => Promise<unknown>;
  now?: () => number;
  schedule?: (run: () => void, delay: number) => unknown;
  cancel?: (handle: unknown) => void;
  isVisible?: () => boolean;
  foregroundHorizonMs?: number;
  visibleDelayMs?: number;
  hiddenDelayMs?: number;
  [key: string]: unknown;
}

declare module "*/shared/saved-sync-core.js" {
  export function captureSavedFocus(
    root: unknown,
    activeElement: unknown,
  ): ObscSavedFocusToken | null;
  export function restoreSavedFocus(
    root: unknown,
    token: ObscSavedFocusToken | null | undefined,
  ): boolean;
  export function createDialogFocusController(
    options?: ObscDialogFocusControllerOptions,
  ): ObscDialogFocusController;
  export function createDurableTaskTracker(
    options?: ObscDurableTaskTrackerOptions,
  ): ObscDurableTaskTracker;
  export function createRetainedSavedListState(): ObscRetainedSavedListState;
  export function createSavedMutationRegistry(): ObscSavedMutationRegistry;
  export function createSavedSubmissionFence(): ObscSavedSubmissionFence;
  export function createSavedTaskCoordinator(options?: {
    tracker?: unknown;
    fetchTask?: (taskId: string) => Promise<unknown>;
    onTerminal?: (task: ObscSavedSyncTask) => void;
    onProgress?: (task: ObscSavedSyncTask) => void;
    onBackground?: (task: ObscSavedSyncTask) => void;
    onPollError?: (error: unknown, task?: ObscSavedSyncTask) => void;
  }): ObscSavedTaskCoordinator;
  export function isSavedTaskTerminal(task: unknown): boolean;
}

// ── model-config-state.js ───────────────────────────────────

/** Mobile model editor draft state (opaque shared-module shape). */
interface ObscModelEditorState {
  models: {
    chat: {
      concurrency: number;
      timeout_seconds: number;
      connections: ObscModelConnectionRecord[];
      [key: string]: unknown;
    };
    embedding: {
      enabled: boolean;
      settings: {
        model: string;
        output_dimensionality: number;
        similarity_threshold: number;
        multimodal_enabled: boolean;
        [key: string]: unknown;
      };
      providers: ObscModelConnectionRecord[];
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
  selected: Record<string, string>;
  activeRoute: string;
  revision: string;
  dirty: boolean;
  remoteUpdate?: { snapshot: unknown; [key: string]: unknown } | null;
  overrides?: Array<{ path: string; source: string; [key: string]: unknown }>;
  overrideLocks?: Record<string, unknown>;
  fieldErrors?: {
    global?: Array<{ path: string; message: string; [key: string]: unknown }>;
    byConnection?: Record<string, Record<string, { path: string; message: string; [key: string]: unknown }>>;
  };
  migration?: { issues?: ObscMigrationIssue[]; [key: string]: unknown };
  migration_resolutions?: Record<string, { action: string; [key: string]: unknown }>;
  [key: string]: unknown;
}

interface ObscModelConnectionRecord {
  id: string;
  name: string;
  type: string;
  preset?: string;
  base_url?: string;
  model?: string;
  api_mode?: string;
  reasoning_effort?: string;
  http_referer?: string;
  x_title?: string;
  num_ctx?: number;
  credential?: { action: string; value: string; [key: string]: unknown };
  circuit?: { state?: string; failure_kind?: string; [key: string]: unknown } | null;
  probe?: {
    ok?: boolean;
    error_code?: string;
    observed_dimension?: number;
    latency_ms?: number;
    probed_at?: string;
    [key: string]: unknown;
  } | null;
  [key: string]: unknown;
}

interface ObscModelDescriptorField {
  key: string;
  label?: string;
  [key: string]: unknown;
}

interface ObscModelPreset {
  id: string;
  label?: string;
  capabilities?: string[];
  [key: string]: unknown;
}

interface ObscModelDescriptor {
  id: string;
  label?: string;
  category?: string;
  capabilities?: string[];
  fields: ObscModelDescriptorField[];
  preset_definitions?: ObscModelPreset[];
  [key: string]: unknown;
}

interface ObscConnectionTypesPayload {
  connection_types: ObscModelDescriptor[];
  groups: unknown[];
  [key: string]: unknown;
}

interface ObscMigrationIssue {
  id: string;
  code?: string;
  reason?: string;
  field?: string;
  provider?: string;
  allowed_actions?: string[];
  [key: string]: unknown;
}

interface ObscProbeSignature {
  kind: string;
  id: string;
  revision: string;
  fingerprint: string;
  [key: string]: unknown;
}

interface ObscModelOperationGate {
  saveInFlight: boolean;
  probeInFlight: boolean;
  controlState(): { saveDisabled: boolean; probeDisabled: boolean };
  beginSave(): { generation: number; invalidatedProbe: boolean; [key: string]: unknown } | null;
  finishSave(generation: number): boolean;
  beginProbe(): number;
  isProbeCurrent(generation: number): boolean;
  finishProbe(generation: number): void;
}

interface ObscLatestRequestGate {
  begin(): number;
  isCurrent(generation: number): boolean;
  invalidate(): void;
}

declare module "*/shared/model-config-state.js" {
  export const MAX_ROUTE_ITEMS: number;

  export function applyLatestSnapshotRequest(options: {
    gate: ObscLatestRequestGate;
    request?: () => Promise<unknown>;
    blocked?: () => boolean;
    onBlocked?: (snapshot: unknown) => void;
    apply?: (snapshot: unknown) => void;
    [key: string]: unknown;
  }): Promise<unknown>;
  export function createLatestRequestGate(): ObscLatestRequestGate;
  export function loadIndependentModelResources(options: {
    gate: ObscLatestRequestGate;
    descriptorGate: ObscLatestRequestGate;
    snapshotRequest?: () => Promise<unknown>;
    descriptorRequest?: () => Promise<unknown>;
    blocked?: () => boolean;
    onSnapshotBlocked?: (snapshot: unknown) => void;
    applySnapshot?: (snapshot: unknown) => void;
    installDescriptors?: (descriptors: unknown) => void;
    [key: string]: unknown;
  }): Promise<unknown>;

  export function appendRouteItem(
    state: ObscModelEditorState,
    kind: string,
    record: ObscModelConnectionRecord,
  ): ObscModelEditorState;
  export function applyPreset(
    state: ObscModelEditorState,
    kind: string,
    recordId: string,
    preset: ObscModelPreset,
    options?: { previousPreset?: ObscModelPreset | null; [key: string]: unknown },
  ): ObscModelEditorState;
  export function applyProbeResult(
    state: ObscModelEditorState,
    signature: ObscProbeSignature,
    result: unknown,
  ): { state: ObscModelEditorState; [key: string]: unknown };
  export function changeConnectionType(
    state: ObscModelEditorState,
    kind: string,
    recordId: string,
    descriptor: ObscModelDescriptor,
    options?: { confirmed?: boolean; previousDescriptor?: ObscModelDescriptor | null },
  ): { state: ObscModelEditorState; incompatibleFields: string[]; [key: string]: unknown };
  export function changePreset(
    state: ObscModelEditorState,
    kind: string,
    recordId: string,
    descriptor: ObscModelDescriptor,
    preset: ObscModelPreset,
    options?: { confirmed?: boolean; [key: string]: unknown },
  ): { state: ObscModelEditorState; incompatibleFields: string[]; [key: string]: unknown };
  export function circuitView(
    record: ObscModelConnectionRecord | null | undefined,
  ): { label: string; [key: string]: unknown } | null;
  export function createModelOperationGate(): ObscModelOperationGate;
  export function createProbeSignature(
    state: ObscModelEditorState,
    kind: string,
    recordId: string,
  ): ObscProbeSignature;
  export function hasUnverifiedChanges(
    state: ObscModelEditorState,
    kind: string,
    recordId: string,
  ): boolean;
  export function hydrateModelConfig(snapshot: unknown): ObscModelEditorState;
  export function mapServerFieldErrors(
    state: ObscModelEditorState,
    errors: unknown,
  ): ObscModelEditorState;
  export function moveRouteItem(
    state: ObscModelEditorState,
    kind: string,
    recordId: string,
    targetIndex: number,
  ): ObscModelEditorState;
  export function probeSignatureMatches(
    state: ObscModelEditorState,
    signature: ObscProbeSignature,
  ): boolean;
  export function receiveRemoteSnapshot(
    state: ObscModelEditorState,
    snapshot: unknown,
  ): ObscModelEditorState;
  export function removeRouteItem(
    state: ObscModelEditorState,
    kind: string,
    recordId: string,
  ): ObscModelEditorState;
  export function selectRouteItem(
    state: ObscModelEditorState,
    kind: string,
    recordId: string,
  ): ObscModelEditorState;
  export function selectedRecord(
    state: ObscModelEditorState | null | undefined,
    kind: string,
  ): ObscModelConnectionRecord | null;
  export function setMigrationResolution(
    state: ObscModelEditorState,
    issueId: string,
    resolution: { action: string; [key: string]: unknown },
  ): ObscModelEditorState;
  export function toModelConfigPayload(state: ObscModelEditorState): ObscModelEditorState;
  export function unverifiedConnections(
    state: ObscModelEditorState,
  ): ObscModelConnectionRecord[];
  export function updateRouteField(
    state: ObscModelEditorState,
    kind: string,
    recordId: string,
    field: string,
    value: unknown,
  ): ObscModelEditorState;
  export function updateRouteSetting(
    state: ObscModelEditorState,
    kind: string,
    field: string,
    value: unknown,
  ): ObscModelEditorState;
}

// ── model-config-render.js ──────────────────────────────────

declare module "*/shared/model-config-render.js" {
  export function applyTypeOptionRovingTabindex(host: Element): void;
  export function disabledMarkup(locked: boolean): string;
  export function escapeHtml(value: unknown): string;
  export function moveTypeOptionFocus(event: KeyboardEvent): void;
  export function renderConnectionTypeGroups(options: {
    groups: unknown[];
    record: ObscModelConnectionRecord;
    kind: string;
    locked: boolean;
    query?: string;
    classPrefix?: string;
    [key: string]: unknown;
  }): string;
  export function renderCredentialEditor(options: {
    record: ObscModelConnectionRecord;
    descriptor: ObscModelDescriptor | null;
    kind: string;
    locked: boolean;
    errorMarkup: (recordId: string, field: string) => string;
    fieldClass?: string;
    credentialValueId?: string;
    noteClass?: string;
    classPrefix?: string;
    [key: string]: unknown;
  }): { hidden: boolean; html: string };
  export function renderDescriptorField(options: {
    record: ObscModelConnectionRecord;
    descriptor: ObscModelDescriptor | null;
    field: ObscModelDescriptorField;
    kind: string;
    locked: boolean;
    errorMarkup: (recordId: string, field: string) => string;
    fieldClass?: string;
    fullWidthFields?: boolean;
    numCtxDescribedBy?: string;
    [key: string]: unknown;
  }): string;
}
