(function installPendingActions(global) {
  "use strict";

  function createPendingActionCoordinator(options = {}) {
    const windowMs = Number(options.windowMs ?? 10000);
    const setTimer = options.setTimer || global.setTimeout.bind(global);
    const clearTimer = options.clearTimer || global.clearTimeout.bind(global);
    const onCommitError = options.onCommitError || (() => {});
    const entries = new Map();

    function finish(key, { keepalive = false } = {}) {
      const entry = entries.get(key);
      if (!entry || entry.state !== "pending") {
        return entry?.promise || Promise.resolve(false);
      }
      entry.state = "committing";
      clearTimer(entry.timerId);
      let commitResult;
      try {
        // Keep the actual fetch call in the pagehide event stack. Deferring it
        // to a microtask can let the document unload before keepalive starts.
        commitResult = entry.commit({ keepalive });
      } catch (error) {
        entry.state = "rolled_back";
        entries.delete(key);
        entry.rollback({ reason: "error", error });
        onCommitError(error, key);
        entry.promise = Promise.resolve(false);
        return entry.promise;
      }
      entry.promise = Promise.resolve(commitResult)
        .then(() => {
          entry.state = "committed";
          entries.delete(key);
          entry.committed?.();
          return true;
        })
        .catch((error) => {
          entry.state = "rolled_back";
          entries.delete(key);
          entry.rollback({ reason: "error", error });
          onCommitError(error, key);
          return false;
        });
      return entry.promise;
    }

    function schedule(key, action) {
      if (!key || entries.has(key)) return false;
      if (typeof action?.commit !== "function" || typeof action?.rollback !== "function") {
        throw new TypeError("pending action requires commit and rollback callbacks");
      }
      const entry = {
        ...action,
        key,
        state: "pending",
        promise: null,
        timerId: null,
      };
      entry.timerId = setTimer(() => {
        void finish(key);
      }, windowMs);
      entries.set(key, entry);
      return true;
    }

    function undo(key) {
      const entry = entries.get(key);
      if (!entry || entry.state !== "pending") return false;
      clearTimer(entry.timerId);
      entries.delete(key);
      entry.state = "rolled_back";
      entry.rollback({ reason: "undo", error: null });
      return true;
    }

    function flushAll() {
      return Promise.all(
        [...entries.keys()].map((key) => finish(key, { keepalive: true })),
      );
    }

    return {
      schedule,
      undo,
      flushAll,
      has: (key) => entries.has(key),
      get: (key) => entries.get(key) || null,
    };
  }

  const api = { createPendingActionCoordinator };
  global.OpenBiliClawPendingActions = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
