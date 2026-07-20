import {
  applyLatestSnapshotRequest,
  createLatestRequestGate,
  loadIndependentModelResources,
} from "../shared/model-config-state.js";

// TODO(types): replace these provisional UI/backend boundaries with generated
// model-config contracts once the API schema is exported for the web client.
type OpaqueRecord = Record<string, unknown>;

interface ResourceReadiness {
  snapshotReady: boolean;
  descriptorsReady: boolean;
  ready: boolean;
  loading: boolean;
}

interface MobileModelResourceOptions {
  snapshotGate?: ObscLatestRequestGate;
  descriptorGate?: ObscLatestRequestGate;
  snapshotRequest: () => Promise<unknown>;
  descriptorRequest: () => Promise<unknown>;
  onReadinessChange?: (readiness: ResourceReadiness) => void;
  blocked?: (context: { remote: boolean }) => boolean;
  applySnapshot?: (snapshot: unknown, context: { remote: boolean }) => void;
  installDescriptors?: (descriptors: unknown) => void;
  onSnapshotBlocked?: (snapshot: unknown, context: { remote: boolean }) => void;
}

interface MobileModelLoadRecoveryOptions {
  setLocked?: (locked: boolean) => void;
  setBusy?: (busy: boolean) => void;
  setRetryVisible?: (visible: boolean) => void;
  onReady?: (readiness: ResourceReadiness) => void;
  onLoading?: () => void;
  onRecoverableIncomplete?: (readiness: ResourceReadiness) => void;
  onError?: (error: unknown, readiness: ResourceReadiness) => void;
}

interface ExactDraftRenderOptions {
  clearInlineErrors?: () => void;
  renderErrorSummary?: () => void;
  renderRouteList?: () => void;
  renderInspector?: () => void;
  renderCredential?: () => void;
  renderProbeStatus?: () => void;
}

interface NumericError {
  path: string;
  field: string;
  route: string;
  connectionId: string;
  message: string;
}

interface NumericValidationResult {
  valid: boolean;
  byPath: Record<string, NumericError>;
  byConnection: Record<string, Record<string, NumericError>>;
  firstError: NumericError | null;
}

interface MobileNumericState extends OpaqueRecord {
  models?: {
    chat?: OpaqueRecord & { connections?: Array<OpaqueRecord> };
    embedding?: OpaqueRecord & {
      settings?: OpaqueRecord;
      providers?: Array<OpaqueRecord>;
    };
  };
}

interface NumericValidationControllerOptions {
  getState?: () => MobileNumericState | null | undefined;
  renderErrors?: (errors: NumericValidationResult) => void;
  focusFirstError?: (error: NumericError | null) => void;
}

/**
 * Own the independently versioned snapshot and descriptor loads used by the
 * mobile Models editor. A successful sibling stays ready when the other fails.
 */
export function createMobileModelResourceCoordinator(options: MobileModelResourceOptions) {
  const snapshotGate = options.snapshotGate || createLatestRequestGate();
  const descriptorGate = options.descriptorGate || createLatestRequestGate();
  let snapshotReady = false;
  let descriptorsReady = false;
  let loading = false;
  let enterPromise: Promise<ResourceReadiness> | null = null;
  let invalidated = false;

  const readiness = (): ResourceReadiness => ({
    snapshotReady,
    descriptorsReady,
    ready: snapshotReady && descriptorsReady,
    loading,
  });
  const notify = () => options.onReadinessChange?.(readiness());
  const blocked = (remote: boolean) => Boolean(options.blocked?.({ remote }));

  const applySnapshot = (snapshot: unknown, remote: boolean) => {
    if (invalidated) return;
    options.applySnapshot?.(snapshot, { remote });
    snapshotReady = true;
    notify();
  };
  const installDescriptors = (descriptors: unknown) => {
    if (invalidated) return;
    options.installDescriptors?.(descriptors);
    descriptorsReady = true;
    notify();
  };

  async function loadSnapshot(remote = false): Promise<unknown> {
    return applyLatestSnapshotRequest({
      gate: snapshotGate,
      request: options.snapshotRequest,
      blocked: () => invalidated || blocked(remote),
      onBlocked: (snapshot) => options.onSnapshotBlocked?.(snapshot, { remote }),
      apply: (snapshot) => applySnapshot(snapshot, remote),
    });
  }

  async function loadDescriptors(): Promise<boolean> {
    const generation = descriptorGate.begin();
    let descriptors;
    try {
      descriptors = await options.descriptorRequest();
    } catch (error) {
      if (invalidated || !descriptorGate.isCurrent(generation)) return false;
      throw error;
    }
    if (invalidated || !descriptorGate.isCurrent(generation)) return false;
    installDescriptors(descriptors);
    return true;
  }

  async function loadBoth(): Promise<unknown> {
    return loadIndependentModelResources({
      gate: snapshotGate,
      descriptorGate,
      snapshotRequest: options.snapshotRequest,
      descriptorRequest: options.descriptorRequest,
      blocked: () => invalidated || blocked(false),
      onSnapshotBlocked: (snapshot) => (
        options.onSnapshotBlocked?.(snapshot, { remote: false })
      ),
      applySnapshot: (snapshot) => applySnapshot(snapshot, false),
      installDescriptors,
    });
  }

  return {
    readiness,
    async enterModels() {
      if (invalidated) return readiness();
      if (enterPromise) return enterPromise;
      if (snapshotReady && descriptorsReady) return readiness();
      loading = true;
      notify();
      const needsSnapshot = !snapshotReady;
      const needsDescriptors = !descriptorsReady;
      enterPromise = (async () => {
        try {
          if (needsSnapshot && needsDescriptors) await loadBoth();
          else if (needsSnapshot) await loadSnapshot(false);
          else await loadDescriptors();
        } finally {
          loading = false;
          enterPromise = null;
          notify();
        }
        return readiness();
      })();
      return enterPromise;
    },
    reloadSnapshot() {
      if (invalidated) return Promise.resolve(false);
      return loadSnapshot(true);
    },
    invalidateSnapshotRequests() {
      snapshotGate.invalidate();
    },
    invalidate() {
      invalidated = true;
      snapshotGate.invalidate();
      descriptorGate.invalidate();
      loading = false;
      enterPromise = null;
      notify();
    },
  };
}

/**
 * Project resource readiness into recoverable UI feedback. An entry attempt
 * that settles without both resources is a visible failure even when its
 * underlying stale request was correctly ignored.
 */
export function createMobileModelLoadRecoveryController(options: MobileModelLoadRecoveryOptions = {}) {
  function onReadinessChange(readiness: ResourceReadiness): ResourceReadiness {
    const loading = Boolean(readiness?.loading);
    const ready = Boolean(readiness?.ready) && !loading;
    options.setLocked?.(!ready);
    options.setBusy?.(loading);
    if (!ready) return readiness;
    options.setRetryVisible?.(false);
    options.onReady?.(readiness);
    return readiness;
  }

  return {
    onReadinessChange,
    beginEntry() {
      options.setLocked?.(true);
      options.setBusy?.(true);
      options.setRetryVisible?.(false);
      options.onLoading?.();
    },
    settleEntry(readiness: ResourceReadiness) {
      onReadinessChange(readiness);
      options.setBusy?.(false);
      if (!readiness?.ready) {
        options.setRetryVisible?.(true);
        options.onRecoverableIncomplete?.(readiness);
      }
      return readiness;
    },
    failEntry(error: unknown, readiness: ResourceReadiness) {
      onReadinessChange(readiness);
      options.setBusy?.(false);
      options.setRetryVisible?.(true);
      options.onError?.(error, readiness);
      return readiness;
    },
  };
}

/** Read a stable-ID field error without consulting object prototypes. */
export function readOwnMobileModelFieldError(
  byConnection: unknown,
  recordId: string,
  field: string,
): unknown {
  if (
    !byConnection
    || typeof byConnection !== "object"
    || !Object.hasOwn(byConnection, recordId)
  ) return null;
  const fields = (byConnection as OpaqueRecord)[recordId];
  if (!fields || typeof fields !== "object" || !Object.hasOwn(fields, field)) return null;
  return (fields as OpaqueRecord)[field] || null;
}

/**
 * Refresh every derived exact-draft surface without rebuilding the live input
 * unless a structural choice (such as a preset) actually requires it.
 */
export function createExactDraftRenderCoordinator(options: ExactDraftRenderOptions = {}) {
  function refresh({
    rebuildInspector = false,
    rerenderCredential = false,
    clearInlineErrors = true,
  }: {
    rebuildInspector?: boolean;
    rerenderCredential?: boolean;
    clearInlineErrors?: boolean;
  } = {}) {
    if (clearInlineErrors) options.clearInlineErrors?.();
    options.renderErrorSummary?.();
    options.renderRouteList?.();
    if (rebuildInspector) options.renderInspector?.();
    else if (rerenderCredential) options.renderCredential?.();
    options.renderProbeStatus?.();
  }

  return {
    afterDraftMutation(optionsForRefresh: Parameters<typeof refresh>[0] = {}) {
      refresh(optionsForRefresh);
    },
    beforeRouteList() {
      refresh({ clearInlineErrors: false });
    },
  };
}

/** Validate the server's Runtime integer bounds before issuing a model PUT. */
export function guardMobileModelRuntime(
  chat: OpaqueRecord & { connections?: Array<OpaqueRecord> },
  showErrors: (errors: Record<string, string>) => void = () => {},
) {
  const validation = validateMobileModelNumbers({
    models: {
      chat: { ...chat, connections: chat?.connections || [] },
      embedding: {
        settings: {
          output_dimensionality: 0,
          similarity_threshold: 0,
        },
      },
    },
  });
  const errors: Record<string, string> = {};
  for (const [field, path] of [
    ["concurrency", "models.chat.concurrency"],
    ["timeout_seconds", "models.chat.timeout_seconds"],
  ]) {
    if (validation.byPath[path]) errors[field] = validation.byPath[path].message;
  }
  showErrors(errors);
  return Object.keys(errors).length === 0;
}

const NUMERIC_MESSAGES: Record<string, string> = {
  concurrency: "Chat 并发数必须是 1 到 16 之间的整数。",
  timeout_seconds: "整条 route 超时必须是至少 10 秒的整数。",
  num_ctx: "Context window 必须是大于或等于 0 的整数。",
  output_dimensionality: "Embedding 输出维度必须是大于或等于 0 的整数。",
  similarity_threshold: "Embedding 相似度阈值必须是 0 到 1 之间的有限数值。",
};

/** Preserve an emptied number input as an invalid draft instead of coercing it to zero. */
export function parseMobileModelNumericDraft(rawValue: unknown): number | "" {
  const value = String(rawValue ?? "");
  return value.trim() ? Number(value) : "";
}

function emptyNumericErrors(): NumericValidationResult {
  return {
    valid: true,
    byPath: {},
    byConnection: {},
    firstError: null,
  };
}

function ownErrorBucket(
  container: Record<string, Record<string, NumericError>>,
  key: string,
): Record<string, NumericError> {
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

function addNumericError(result: NumericValidationResult, {
  path,
  field,
  route,
  connectionId = "",
  message,
}: Omit<NumericError, "connectionId"> & { connectionId?: string }): void {
  const error = {
    path,
    field,
    route,
    connectionId,
    message,
  };
  result.valid = false;
  result.byPath[path] = error;
  if (connectionId) {
    const fields = ownErrorBucket(result.byConnection, connectionId);
    Object.defineProperty(fields, field, {
      value: error,
      configurable: true,
      enumerable: true,
      writable: true,
    });
  }
  result.firstError ||= error;
}

/** Validate every strict numeric field before the mobile editor serializes a PUT. */
export function validateMobileModelNumbers(
  state: MobileNumericState | null | undefined,
): NumericValidationResult {
  const result = emptyNumericErrors();
  const chat = state?.models?.chat || {};
  const embedding = state?.models?.embedding?.settings || {};
  const concurrency = chat.concurrency as number;
  const timeoutSeconds = chat.timeout_seconds as number;
  const outputDimensionality = embedding.output_dimensionality as number;

  if (
    !Number.isInteger(concurrency)
    || concurrency < 1
    || concurrency > 16
  ) {
    addNumericError(result, {
      path: "models.chat.concurrency",
      field: "concurrency",
      route: "runtime",
      message: NUMERIC_MESSAGES.concurrency,
    });
  }
  if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 10) {
    addNumericError(result, {
      path: "models.chat.timeout_seconds",
      field: "timeout_seconds",
      route: "runtime",
      message: NUMERIC_MESSAGES.timeout_seconds,
    });
  }
  for (const [index, connection] of (chat.connections || []).entries()) {
    const numCtx = connection?.num_ctx as number;
    if (Number.isInteger(numCtx) && numCtx >= 0) continue;
    addNumericError(result, {
      path: `models.chat.connections.${index}.num_ctx`,
      field: "num_ctx",
      route: "chat",
      connectionId: String(connection?.id || ""),
      message: NUMERIC_MESSAGES.num_ctx,
    });
  }
  if (
    !Number.isInteger(outputDimensionality)
    || outputDimensionality < 0
  ) {
    addNumericError(result, {
      path: "models.embedding.settings.output_dimensionality",
      field: "output_dimensionality",
      route: "embedding",
      message: NUMERIC_MESSAGES.output_dimensionality,
    });
  }
  if (
    typeof embedding.similarity_threshold !== "number"
    || !Number.isFinite(embedding.similarity_threshold)
    || embedding.similarity_threshold < 0
    || embedding.similarity_threshold > 1
  ) {
    addNumericError(result, {
      path: "models.embedding.settings.similarity_threshold",
      field: "similarity_threshold",
      route: "embedding",
      message: NUMERIC_MESSAGES.similarity_threshold,
    });
  }
  return result;
}

/** Keep local numeric feedback derived from the current draft, never historical edits. */
export function createMobileModelNumericValidationController(
  options: NumericValidationControllerOptions = {},
) {
  let current = emptyNumericErrors();

  function revalidate(
    state: MobileNumericState | null | undefined = options.getState?.(),
  ): NumericValidationResult {
    current = validateMobileModelNumbers(state);
    options.renderErrors?.(current);
    return current;
  }

  function runIfValid(
    state: MobileNumericState | null | undefined,
    callback: (() => void) | null | undefined,
  ): boolean {
    const errors = revalidate(state);
    if (!errors.valid) {
      options.focusFirstError?.(errors.firstError);
      return false;
    }
    callback?.();
    return true;
  }

  return {
    errors() {
      return current;
    },
    afterDraftMutation(state: MobileNumericState | null | undefined) {
      return revalidate(state);
    },
    afterAuthoritativeHydration(state: MobileNumericState | null | undefined) {
      return revalidate(state);
    },
    runSaveIfValid(
      state: MobileNumericState | null | undefined,
      save: (() => void) | null | undefined,
    ) {
      return runIfValid(state, save);
    },
    runProbeIfValid(
      state: MobileNumericState | null | undefined,
      probe: (() => void) | null | undefined,
    ) {
      return runIfValid(state, probe);
    },
  };
}

function normalizedValidationPath(rawLocation: unknown): string {
  const location = Array.isArray(rawLocation) ? [...rawLocation] : [];
  if (location[0] === "body") location.shift();
  const segments = location.map((segment) => {
    if (Number.isInteger(segment) && segment >= 0) return String(segment);
    const value = String(segment || "");
    return /^[A-Za-z_][A-Za-z0-9_]*$/.test(value) ? value : "field";
  });
  return segments.join(".") || "models";
}

function validationConnectionId(
  path: string,
  state: MobileNumericState | null | undefined,
): string {
  const parts = path.split(".");
  const chatIndex = parts[0] === "models"
    && parts[1] === "chat"
    && parts[2] === "connections"
    ? Number(parts[3])
    : Number.NaN;
  if (Number.isInteger(chatIndex)) {
    return String(state?.models?.chat?.connections?.[chatIndex]?.id || "");
  }
  const embeddingIndex = parts[0] === "models"
    && parts[1] === "embedding"
    && parts[2] === "providers"
    ? Number(parts[3])
    : Number.NaN;
  if (Number.isInteger(embeddingIndex)) {
    return String(state?.models?.embedding?.providers?.[embeddingIndex]?.id || "");
  }
  return "";
}

function validationMessage(path: string): string {
  if (path === "models.chat.concurrency") return NUMERIC_MESSAGES.concurrency;
  if (path === "models.chat.timeout_seconds") return NUMERIC_MESSAGES.timeout_seconds;
  if (/^models\.chat\.connections\.\d+\.num_ctx$/.test(path)) {
    return NUMERIC_MESSAGES.num_ctx;
  }
  if (path === "models.embedding.settings.output_dimensionality") {
    return NUMERIC_MESSAGES.output_dimensionality;
  }
  if (path === "models.embedding.settings.similarity_threshold") {
    return NUMERIC_MESSAGES.similarity_threshold;
  }
  return "模型配置字段无效。";
}

/** Convert secret-safe Pydantic details to the editor's stable field-error contract. */
export function normalizeMobileModelValidationDetails(
  details: unknown,
  state: MobileNumericState | null | undefined,
) {
  return (Array.isArray(details) ? details : []).map((raw) => {
    const path = normalizedValidationPath(raw?.loc);
    const rawCode = String(raw?.type || "validation_failed");
    const code = /^[A-Za-z0-9_.]+$/.test(rawCode) ? rawCode : "validation_failed";
    const connectionId = validationConnectionId(path, state);
    return {
      path,
      code,
      message: validationMessage(path),
      ...(connectionId ? { connection_id: connectionId } : {}),
    };
  });
}
