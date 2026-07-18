export const OFFLINE_BACKEND_POLL_INTERVAL_MS = 1000;

export const BACKEND_CONNECTION_STATUS = Object.freeze({
  ONLINE: "online",
  RECONNECTING: "reconnecting",
  OFFLINE: "offline",
});

/**
 * Coordinate HTTP reachability and the runtime WebSocket without letting stale
 * async probes overwrite a newer stream connection.
 */
export function createBackendConnectionCoordinator({
  checkBackendStatus,
  onStatusChange = () => {},
} = {}) {
  if (typeof checkBackendStatus !== "function") {
    throw new TypeError("checkBackendStatus must be a function");
  }

  let revision = 0;
  let status = null;
  let hasStreamConnected = false;

  function publish(nextStatus) {
    if (status === nextStatus) return;
    status = nextStatus;
    onStatusChange(nextStatus);
  }

  function mark(nextStatus) {
    revision += 1;
    publish(nextStatus);
    return status;
  }

  return {
    getStatus() {
      return status ?? BACKEND_CONNECTION_STATUS.OFFLINE;
    },
    markHttpReachable() {
      return mark(
        status === BACKEND_CONNECTION_STATUS.ONLINE
          ? BACKEND_CONNECTION_STATUS.ONLINE
          : BACKEND_CONNECTION_STATUS.RECONNECTING,
      );
    },
    markOffline() {
      return mark(BACKEND_CONNECTION_STATUS.OFFLINE);
    },
    markStreamConnected() {
      const reconnected = hasStreamConnected;
      hasStreamConnected = true;
      return {
        reconnected,
        status: mark(BACKEND_CONNECTION_STATUS.ONLINE),
      };
    },
    async markStreamDisconnected() {
      const probeRevision = ++revision;
      publish(BACKEND_CONNECTION_STATUS.RECONNECTING);

      let reachable = false;
      try {
        reachable = Boolean(await checkBackendStatus());
      } catch {
        reachable = false;
      }

      if (probeRevision !== revision) {
        return {
          applied: false,
          reachable,
          status: status ?? BACKEND_CONNECTION_STATUS.OFFLINE,
        };
      }

      if (!reachable) {
        publish(BACKEND_CONNECTION_STATUS.OFFLINE);
      }
      return {
        applied: true,
        reachable,
        status: status ?? BACKEND_CONNECTION_STATUS.OFFLINE,
      };
    },
  };
}

export function createOfflineBackendPoller({
  isOnline,
  checkBackendStatus,
  onOnline,
  setTimeoutImpl = globalThis.setTimeout,
  clearTimeoutImpl = globalThis.clearTimeout,
  delayMs = OFFLINE_BACKEND_POLL_INTERVAL_MS,
} = {}) {
  let timer = null;
  let inFlight = false;
  let stopped = false;

  function shouldPoll() {
    return !stopped && !(typeof isOnline === "function" && isOnline());
  }

  function clearTimer() {
    if (timer === null) return;
    clearTimeoutImpl(timer);
    timer = null;
  }

  function schedule() {
    if (!shouldPoll() || timer !== null) return;
    timer = setTimeoutImpl(() => runProbe(), delayMs);
  }

  async function runProbe() {
    timer = null;
    if (!shouldPoll()) return;
    if (inFlight) {
      schedule();
      return;
    }

    inFlight = true;
    let online = false;
    try {
      online = Boolean(await checkBackendStatus());
    } catch {
      online = false;
    } finally {
      inFlight = false;
    }

    if (!shouldPoll()) return;
    if (online) {
      clearTimer();
      await onOnline?.();
      return;
    }
    schedule();
  }

  return {
    start() {
      stopped = false;
      schedule();
    },
    stop() {
      stopped = true;
      clearTimer();
    },
  };
}
