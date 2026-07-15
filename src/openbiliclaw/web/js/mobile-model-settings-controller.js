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
        if (needsSnapshot && needsDescriptors) return loadBoth();
        if (needsSnapshot) return loadSnapshot(false);
        return loadDescriptors();
      })();
      try {
        await enterPromise;
        return readiness();
      } finally {
        loading = false;
        enterPromise = null;
        notify();
      }
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
  const errors = {};
  const concurrency = Number(chat?.concurrency);
  const timeoutSeconds = Number(chat?.timeout_seconds);
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 16) {
    errors.concurrency = "Chat 并发数必须是 1 到 16 之间的整数。";
  }
  if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 10) {
    errors.timeout_seconds = "整条 route 超时必须是至少 10 秒的整数。";
  }
  showErrors(errors);
  return Object.keys(errors).length === 0;
}
