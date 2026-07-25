import test from "node:test";
import assert from "node:assert/strict";

import * as connectionPollerModule from "../popup/popup-connection-poller.js";

const {
  OFFLINE_BACKEND_POLL_INTERVAL_MS,
  createBackendConnectionCoordinator,
  createOfflineBackendPoller,
} = connectionPollerModule;

test("connection coordinator keeps a reachable backend in reconnecting state", async () => {
  assert.equal(typeof createBackendConnectionCoordinator, "function");
  const statuses: string[] = [];
  const coordinator = createBackendConnectionCoordinator({
    checkBackendStatus: async () => true,
    onStatusChange: (status: string) => statuses.push(status),
  });

  coordinator.markStreamConnected();
  const result = await coordinator.markStreamDisconnected();

  assert.deepEqual(statuses, ["online", "reconnecting"]);
  assert.deepEqual(result, {
    applied: true,
    reachable: true,
    status: "reconnecting",
  });
});

test("connection coordinator marks the backend offline only after a failed probe", async () => {
  assert.equal(typeof createBackendConnectionCoordinator, "function");
  const statuses: string[] = [];
  const coordinator = createBackendConnectionCoordinator({
    checkBackendStatus: async () => false,
    onStatusChange: (status: string) => statuses.push(status),
  });

  coordinator.markStreamConnected();
  const result = await coordinator.markStreamDisconnected();

  assert.deepEqual(statuses, ["online", "reconnecting", "offline"]);
  assert.deepEqual(result, {
    applied: true,
    reachable: false,
    status: "offline",
  });
});

test("connection coordinator treats a probe error as backend offline", async () => {
  assert.equal(typeof createBackendConnectionCoordinator, "function");
  const statuses: string[] = [];
  const coordinator = createBackendConnectionCoordinator({
    checkBackendStatus: async () => {
      throw new Error("backend down");
    },
    onStatusChange: (status: string) => statuses.push(status),
  });

  coordinator.markStreamConnected();
  const result = await coordinator.markStreamDisconnected();

  assert.deepEqual(statuses, ["online", "reconnecting", "offline"]);
  assert.deepEqual(result, {
    applied: true,
    reachable: false,
    status: "offline",
  });
});

test("connection coordinator ignores a stale disconnect probe after reconnect", async () => {
  assert.equal(typeof createBackendConnectionCoordinator, "function");
  let resolveProbe: ((reachable: boolean) => void) | undefined;
  const statuses: string[] = [];
  const coordinator = createBackendConnectionCoordinator({
    checkBackendStatus: () =>
      new Promise<boolean>((resolve) => {
        resolveProbe = resolve;
      }),
    onStatusChange: (status: string) => statuses.push(status),
  });

  coordinator.markStreamConnected();
  const pendingResult = coordinator.markStreamDisconnected();
  coordinator.markStreamConnected();
  assert.ok(resolveProbe);
  resolveProbe(false);

  assert.deepEqual(await pendingResult, {
    applied: false,
    reachable: false,
    status: "online",
  });
  assert.deepEqual(statuses, ["online", "reconnecting", "online"]);
});

test("connection coordinator treats HTTP recovery as reconnecting until the stream opens", () => {
  assert.equal(typeof createBackendConnectionCoordinator, "function");
  const statuses: string[] = [];
  const coordinator = createBackendConnectionCoordinator({
    checkBackendStatus: async () => true,
    onStatusChange: (status: string) => statuses.push(status),
  });

  coordinator.markOffline();
  coordinator.markHttpReachable();

  assert.deepEqual(statuses, ["offline", "reconnecting"]);
  assert.equal(coordinator.getStatus(), "reconnecting");
});

test("connection coordinator distinguishes the first stream open from a reconnect", () => {
  assert.equal(typeof createBackendConnectionCoordinator, "function");
  const coordinator = createBackendConnectionCoordinator({
    checkBackendStatus: async () => true,
  });

  assert.deepEqual(coordinator.markStreamConnected(), {
    reconnected: false,
    status: "online",
  });
  assert.deepEqual(coordinator.markStreamConnected(), {
    reconnected: true,
    status: "online",
  });
});

test("offline backend poller retries until liveness recovers", async () => {
  const delays: number[] = [];
  const timers = new Map<number, () => Promise<void>>();
  let nextTimerId = 1;
  let online = false;
  const probeResults = [false, true];
  let onlineCallbackCount = 0;

  const poller = createOfflineBackendPoller({
    isOnline: () => online,
    checkBackendStatus: async () => probeResults.shift() ?? false,
    onOnline: async () => {
      online = true;
      onlineCallbackCount += 1;
    },
    setTimeoutImpl(callback: () => Promise<void>, delay: number) {
      const id = nextTimerId++;
      delays.push(delay);
      timers.set(id, callback);
      return id;
    },
    clearTimeoutImpl(id: number) {
      timers.delete(id);
    },
  });

  const runNextTimer = async () => {
    const entry = timers.entries().next().value;
    assert.ok(entry, "expected a scheduled timer");
    const [id, callback] = entry;
    timers.delete(id);
    await callback();
  };

  poller.start();
  assert.deepEqual(delays, [OFFLINE_BACKEND_POLL_INTERVAL_MS]);

  await runNextTimer();
  assert.equal(online, false);
  assert.deepEqual(delays, [
    OFFLINE_BACKEND_POLL_INTERVAL_MS,
    OFFLINE_BACKEND_POLL_INTERVAL_MS,
  ]);

  await runNextTimer();
  assert.equal(online, true);
  assert.equal(onlineCallbackCount, 1);
  assert.equal(timers.size, 0);
});

test("offline backend poller does not schedule while already online", () => {
  const delays: number[] = [];
  const poller = createOfflineBackendPoller({
    isOnline: () => true,
    checkBackendStatus: async () => true,
    onOnline: async () => {},
    setTimeoutImpl(_callback: () => Promise<void>, delay: number) {
      delays.push(delay);
      return 1;
    },
    clearTimeoutImpl() {},
  });

  poller.start();

  assert.deepEqual(delays, []);
});
