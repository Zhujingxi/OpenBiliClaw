import {
  applyLatestSnapshotRequest,
  createLatestRequestGate,
  loadIndependentModelResources,
} from "../shared/model-config-state.js";

/**
 * Own the independently versioned snapshot and descriptor loads used by the
 * mobile Models editor. A successful sibling stays ready when the other fails.
 */
export function createMobileModelResourceCoordinator(options = {}) {
  const snapshotGate = options.snapshotGate || createLatestRequestGate();
  const descriptorGate = options.descriptorGate || createLatestRequestGate();
  let snapshotReady = false;
  let descriptorsReady = false;
  let loading = false;
  let enterPromise = null;
  let invalidated = false;

  const readiness = () => ({
    snapshotReady,
    descriptorsReady,
    ready: snapshotReady && descriptorsReady,
    loading,
  });
  const notify = () => options.onReadinessChange?.(readiness());
  const blocked = (remote) => Boolean(options.blocked?.({ remote }));

  const applySnapshot = (snapshot, remote) => {
    if (invalidated) return;
    options.applySnapshot?.(snapshot, { remote });
    snapshotReady = true;
    notify();
  };
  const installDescriptors = (descriptors) => {
    if (invalidated) return;
    options.installDescriptors?.(descriptors);
    descriptorsReady = true;
    notify();
  };

  async function loadSnapshot(remote = false) {
    return applyLatestSnapshotRequest({
      gate: snapshotGate,
      request: options.snapshotRequest,
      blocked: () => invalidated || blocked(remote),
      onBlocked: (snapshot) => options.onSnapshotBlocked?.(snapshot, { remote }),
      apply: (snapshot) => applySnapshot(snapshot, remote),
    });
  }

  async function loadDescriptors() {
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

  async function loadBoth() {
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
export function createMobileModelLoadRecoveryController(options = {}) {
  function onReadinessChange(readiness) {
    options.setLocked?.(!readiness?.ready);
    if (!readiness?.ready) return readiness;
    options.setRetryVisible?.(false);
    options.onReady?.(readiness);
    return readiness;
  }

  return {
    onReadinessChange,
    beginEntry() {
      options.setRetryVisible?.(false);
      options.onLoading?.();
    },
    settleEntry(readiness) {
      onReadinessChange(readiness);
      if (!readiness?.ready) {
        options.setRetryVisible?.(true);
        options.onRecoverableIncomplete?.(readiness);
      }
      return readiness;
    },
    failEntry(error, readiness) {
      onReadinessChange(readiness);
      options.setRetryVisible?.(true);
      options.onError?.(error, readiness);
      return readiness;
    },
  };
}

/**
 * Refresh every derived exact-draft surface without rebuilding the live input
 * unless a structural choice (such as a preset) actually requires it.
 */
export function createExactDraftRenderCoordinator(options = {}) {
  function refresh({
    rebuildInspector = false,
    rerenderCredential = false,
    clearInlineErrors = true,
  } = {}) {
    if (clearInlineErrors) options.clearInlineErrors?.();
    options.renderErrorSummary?.();
    options.renderRouteList?.();
    if (rebuildInspector) options.renderInspector?.();
    else if (rerenderCredential) options.renderCredential?.();
    options.renderProbeStatus?.();
  }

  return {
    afterDraftMutation(optionsForRefresh = {}) {
      refresh(optionsForRefresh);
    },
    beforeRouteList() {
      refresh({ clearInlineErrors: false });
    },
  };
}

/** Validate the server's Runtime integer bounds before issuing a model PUT. */
export function guardMobileModelRuntime(chat, showErrors = () => {}) {
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
  const errors = {};
  for (const [field, path] of [
    ["concurrency", "models.chat.concurrency"],
    ["timeout_seconds", "models.chat.timeout_seconds"],
  ]) {
    if (validation.byPath[path]) errors[field] = validation.byPath[path].message;
  }
  showErrors(errors);
  return Object.keys(errors).length === 0;
}

const NUMERIC_MESSAGES = {
  concurrency: "Chat 并发数必须是 1 到 16 之间的整数。",
  timeout_seconds: "整条 route 超时必须是至少 10 秒的整数。",
  num_ctx: "Context window 必须是大于或等于 0 的整数。",
  output_dimensionality: "Embedding 输出维度必须是大于或等于 0 的整数。",
  similarity_threshold: "Embedding 相似度阈值必须是 0 到 1 之间的有限数值。",
};

/** Preserve an emptied number input as an invalid draft instead of coercing it to zero. */
export function parseMobileModelNumericDraft(rawValue) {
  const value = String(rawValue ?? "");
  return value.trim() ? Number(value) : "";
}

function emptyNumericErrors() {
  return {
    valid: true,
    byPath: {},
    byConnection: {},
    firstError: null,
  };
}

function ownErrorBucket(container, key) {
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

function addNumericError(result, {
  path,
  field,
  route,
  connectionId = "",
  message,
}) {
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
export function validateMobileModelNumbers(state) {
  const result = emptyNumericErrors();
  const chat = state?.models?.chat || {};
  const embedding = state?.models?.embedding?.settings || {};

  if (
    !Number.isInteger(chat.concurrency)
    || chat.concurrency < 1
    || chat.concurrency > 16
  ) {
    addNumericError(result, {
      path: "models.chat.concurrency",
      field: "concurrency",
      route: "runtime",
      message: NUMERIC_MESSAGES.concurrency,
    });
  }
  if (!Number.isInteger(chat.timeout_seconds) || chat.timeout_seconds < 10) {
    addNumericError(result, {
      path: "models.chat.timeout_seconds",
      field: "timeout_seconds",
      route: "runtime",
      message: NUMERIC_MESSAGES.timeout_seconds,
    });
  }
  for (const [index, connection] of (chat.connections || []).entries()) {
    if (Number.isInteger(connection?.num_ctx) && connection.num_ctx >= 0) continue;
    addNumericError(result, {
      path: `models.chat.connections.${index}.num_ctx`,
      field: "num_ctx",
      route: "chat",
      connectionId: String(connection?.id || ""),
      message: NUMERIC_MESSAGES.num_ctx,
    });
  }
  if (
    !Number.isInteger(embedding.output_dimensionality)
    || embedding.output_dimensionality < 0
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
export function createMobileModelNumericValidationController(options = {}) {
  let current = emptyNumericErrors();

  function revalidate(state = options.getState?.()) {
    current = validateMobileModelNumbers(state);
    options.renderErrors?.(current);
    return current;
  }

  function runIfValid(state, callback) {
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
    afterDraftMutation(state) {
      return revalidate(state);
    },
    afterAuthoritativeHydration(state) {
      return revalidate(state);
    },
    runSaveIfValid(state, save) {
      return runIfValid(state, save);
    },
    runProbeIfValid(state, probe) {
      return runIfValid(state, probe);
    },
  };
}

function normalizedValidationPath(rawLocation) {
  const location = Array.isArray(rawLocation) ? [...rawLocation] : [];
  if (location[0] === "body") location.shift();
  const segments = location.map((segment) => {
    if (Number.isInteger(segment) && segment >= 0) return String(segment);
    const value = String(segment || "");
    return /^[A-Za-z_][A-Za-z0-9_]*$/.test(value) ? value : "field";
  });
  return segments.join(".") || "models";
}

function validationConnectionId(path, state) {
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

function validationMessage(path) {
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
export function normalizeMobileModelValidationDetails(details, state) {
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
