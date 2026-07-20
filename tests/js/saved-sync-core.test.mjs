// @ts-check
import assert from "node:assert/strict";
import test from "node:test";

import {
  captureSavedFocus,
  createDialogFocusController,
  createDurableTaskTracker,
  createRetainedSavedListState,
  createSavedMutationRegistry,
  createSavedSubmissionFence,
  createSavedTaskCoordinator,
  createStrictSavedApi,
  getSavedSyncPresentation,
  isSavedSyncEligibleStatus,
  isSavedTaskTerminal,
  normalizeSavedItem,
  restoreSavedFocus,
  taskIsTerminal,
  updateSavedBatchButtonState,
} from "../../src/openbiliclaw/web/shared/saved-sync-core.js";

/**
 * @template T
 * @returns {{ promise: Promise<T>, resolve: (value: T | PromiseLike<T>) => void, reject: (reason?: unknown) => void }}
 */
function deferred() {
  /** @type {(value: T | PromiseLike<T>) => void} */
  let resolve = () => {};
  /** @type {(reason?: unknown) => void} */
  let reject = () => {};
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function createScheduler() {
  const handles = [];
  return {
    handles,
    schedule(run, delay) {
      const handle = { cancelled: false, delay, run, started: false };
      handles.push(handle);
      return handle;
    },
    cancel(handle) {
      handle.cancelled = true;
    },
    async runNext() {
      const handle = handles.find((candidate) => !candidate.cancelled && !candidate.started);
      assert.ok(handle, "a scheduled callback must be available");
      handle.started = true;
      await handle.run();
      return handle;
    },
  };
}

/**
 * @param {Record<string, any>} [data]
 * @returns {{ dataset: Record<string, any>; focusCalls: number; focus(): void; closest?: (() => any) | null }}
 */
function createAction(data = {}) {
  return {
    dataset: data,
    focusCalls: 0,
    focus() {
      this.focusCalls += 1;
    },
  };
}

/**
 * @param {string} itemKey
 * @param {ReturnType<typeof createAction>[]} actions
 * @returns {{ dataset: { itemKey: string }; querySelectorAll(selector: string): ReturnType<typeof createAction>[] }}
 */
function createCard(itemKey, actions) {
  const card = {
    dataset: { itemKey },
    querySelectorAll(selector) {
      return selector === "[data-saved-action]" ? actions : [];
    },
  };
  for (const action of actions) action.closest = () => card;
  return card;
}

/**
 * @param {any[]} cards
 * @param {any[]} [listActions]
 * @param {any} [heading]
 * @returns {any}
 */
function createFocusRoot(cards, listActions = [], heading = null) {
  return {
    querySelectorAll(selector) {
      return selector === "[data-item-key]" ? cards : [];
    },
    querySelector(selector) {
      if (selector === "[data-saved-heading]") return heading;
      const exact = selector.match(/^\[data-saved-list-action="([^"]+)"\]$/);
      if (exact) {
        return listActions.find((action) => action.dataset.savedListAction === exact[1]) || null;
      }
      if (selector.includes('[data-saved-list-action="sync-all"]')) {
        return (
          listActions.find(
            (action) =>
              action.dataset.savedListAction === "sync-all" ||
              action.dataset.savedListAction === "retry",
          ) || null
        );
      }
      return null;
    },
  };
}

test("submission fences claim normalized keys atomically and release them", () => {
  const fence = createSavedSubmissionFence();

  assert.equal(fence.claim([" youtube:1 ", "youtube:1", "reddit:2"]), true);
  assert.equal(fence.has("youtube:1"), true);
  assert.equal(fence.claim(["zhihu:3", "reddit:2"]), false);
  assert.equal(fence.has("zhihu:3"), false, "a conflicting batch must claim nothing");

  fence.release(["youtube:1", "reddit:2"]);
  assert.equal(fence.claim(["zhihu:3", "reddit:2"]), true);
});

test("retained list state keeps committed rows when a refresh fails", () => {
  const retained = createRetainedSavedListState();
  const row = { item_key: "youtube:1" };

  retained.commit({ items: [row], total: 1 });
  retained.fail(new Error("network_unavailable"));
  const snapshot = retained.snapshot();

  assert.deepEqual(snapshot, {
    items: [row],
    total: 1,
    loaded: true,
    error: "network_unavailable",
  });
  snapshot.items.length = 0;
  assert.equal(retained.snapshot().items.length, 1, "snapshots must not expose the retained array");
});

test("mutation registry rolls back optimistic state and releases its busy fence", async () => {
  const mutation = deferred();
  const registry = createSavedMutationRegistry();
  const operations = {
    add: () => mutation.promise,
    remove: async () => ({ saved: false }),
  };

  const pending = registry.toggle("favorite", "youtube:1", operations);
  assert.equal(registry.isSaved("favorite", "youtube:1"), true);
  assert.equal(registry.isBusy("favorite", "youtube:1"), true);
  assert.equal(await registry.toggle("favorite", "youtube:1", operations), false);

  mutation.reject(new Error("save_failed"));
  await assert.rejects(pending, /save_failed/);
  assert.equal(registry.isSaved("favorite", "youtube:1"), false);
  assert.equal(registry.isBusy("favorite", "youtube:1"), false);
});

test("late status hydration cannot overwrite a newer local mutation", async () => {
  const hydration = deferred();
  const registry = createSavedMutationRegistry();
  const pending = registry.hydrate("watch_later", "bilibili:BV1", () => hydration.promise);

  registry.setSaved("watch_later", "bilibili:BV1", true);
  hydration.resolve({ saved: false });
  await pending;

  assert.equal(registry.isSaved("watch_later", "bilibili:BV1"), true);
});

test("durable task polling changes cadence, survives an error, and reaches terminal state", async () => {
  const scheduler = createScheduler();
  let visible = false;
  let pollCount = 0;
  const events = [];
  const tracker = createDurableTaskTracker({
    poll: async () => {
      pollCount += 1;
      if (pollCount === 1) throw new Error("temporary_poll_failure");
      if (pollCount === 2) {
        return { task_id: "task-1", items: [{ item_key: "youtube:1", status: "syncing" }] };
      }
      return { task_id: "task-1", items: [{ item_key: "youtube:1", status: "synced" }] };
    },
    now: () => 0,
    isVisible: () => visible,
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
    foregroundHorizonMs: 20,
    visibleDelayMs: 5,
    hiddenDelayMs: 50,
  });

  tracker.track(
    { task_id: "task-1", items: [{ item_key: "youtube:1", status: "pending" }] },
    {
      onProgress: (task) => events.push(`progress:${task.items[0].status}`),
      onPollError: (error) => events.push(`error:${error.message}`),
      onTerminal: (task) => events.push(`terminal:${task.items[0].status}`),
    },
  );
  assert.equal(scheduler.handles[0].delay, 50);

  visible = true;
  assert.equal(tracker.resume("task-1"), true);
  assert.equal(scheduler.handles[1].delay, 0);
  await scheduler.runNext();
  assert.equal(scheduler.handles.at(-1).delay, 5);
  await scheduler.runNext();
  await scheduler.runNext();

  assert.deepEqual(events, [
    "progress:pending",
    "error:temporary_poll_failure",
    "progress:syncing",
    "terminal:synced",
  ]);
  assert.equal(tracker.has("task-1"), false);
});

test("task recovery retains ownership when the initial fetch fails and releases it after polling", async () => {
  const scheduler = createScheduler();
  const tracker = createDurableTaskTracker({
    poll: async (taskId) => ({
      task_id: taskId,
      items: [{ item_key: "reddit:abc", status: "already_synced" }],
    }),
    now: () => 0,
    isVisible: () => true,
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  const errors = [];
  const terminal = [];
  const coordinator = createSavedTaskCoordinator({
    tracker,
    fetchTask: async () => {
      throw new Error("recovery_fetch_failed");
    },
  });

  await coordinator.recover(
    [{ item_key: "reddit:abc", sync_status: "syncing", sync_task_id: "task-r" }],
    {
      onPollError: (error) => errors.push(error.message),
      onTerminal: (task) => terminal.push(task.items[0].status),
    },
  );
  assert.equal(coordinator.owns("reddit:abc"), true);
  assert.equal(coordinator.taskFor("reddit:abc"), "task-r");
  assert.deepEqual(errors, ["recovery_fetch_failed"]);

  await scheduler.runNext();
  assert.deepEqual(terminal, ["already_synced"]);
  assert.equal(coordinator.owns("reddit:abc"), false);
});

test("focus capture and restoration prefer the same action then a surviving neighbor", () => {
  const firstOpen = createAction({ savedAction: "open" });
  const firstRemove = createAction({ savedAction: "remove" });
  const secondOpen = createAction({ savedAction: "open" });
  const firstCard = createCard("youtube:1", [firstOpen, firstRemove]);
  const secondCard = createCard("reddit:2", [secondOpen]);
  const root = createFocusRoot([firstCard, secondCard]);

  const token = captureSavedFocus(root, firstRemove);
  assert.deepEqual(token, { itemKey: "youtube:1", action: "remove", index: 0 });
  assert.equal(restoreSavedFocus(root, token), true);
  assert.equal(firstRemove.focusCalls, 1);

  const removedRoot = createFocusRoot([secondCard]);
  assert.equal(restoreSavedFocus(removedRoot, token), true);
  assert.equal(secondOpen.focusCalls, 1);
});

test("list focus restoration falls back through cards and the heading", () => {
  const retry = createAction({ savedListAction: "retry" });
  const heading = createAction();
  const root = createFocusRoot([], [retry], heading);

  const token = captureSavedFocus(root, retry);
  assert.deepEqual(token, { kind: "list", action: "retry" });
  assert.equal(restoreSavedFocus(root, token), true);
  assert.equal(retry.focusCalls, 1);

  assert.equal(
    restoreSavedFocus(createFocusRoot([], [], heading), { kind: "list", action: "sync-all" }),
    true,
  );
  assert.equal(heading.focusCalls, 1);
});

test("dialog focus controller traps Tab, handles Escape, and restores the live opener", () => {
  const listeners = new Map();
  const document = {
    activeElement: null,
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    removeEventListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
  };
  /** @type {any} */
  const first = createAction();
  /** @type {any} */
  const last = createAction();
  first.closest = () => null;
  last.closest = () => null;
  first.focus = () => {
    first.focusCalls += 1;
    document.activeElement = first;
  };
  last.focus = () => {
    last.focusCalls += 1;
    document.activeElement = last;
  };
  const dialog = { querySelectorAll: () => [first, last], focus() {} };
  const liveOpener = createAction();
  let closes = 0;
  const controller = createDialogFocusController({
    dialog,
    document,
    resolveOpener: () => liveOpener,
    onClose: () => {
      closes += 1;
    },
  });

  controller.activate();
  const keydown = listeners.get("keydown");
  document.activeElement = last;
  let prevented = 0;
  keydown({
    key: "Tab",
    shiftKey: false,
    preventDefault: () => {
      prevented += 1;
    },
  });
  assert.equal(first.focusCalls, 1);
  document.activeElement = first;
  keydown({
    key: "Tab",
    shiftKey: true,
    preventDefault: () => {
      prevented += 1;
    },
  });
  assert.equal(last.focusCalls, 1);
  keydown({
    key: "Escape",
    preventDefault: () => {
      prevented += 1;
    },
  });

  assert.equal(prevented, 3);
  assert.equal(closes, 1);
  controller.deactivate();
  assert.equal(liveOpener.focusCalls, 1);
  assert.equal(listeners.has("keydown"), false);
});

test("presentation exposes state keys without embedding surface copy", () => {
  const localOnly = getSavedSyncPresentation({
    sync_status: "unsupported",
    error_code: "unsupported_content_type",
  });
  assert.deepEqual(localOnly, {
    status: "unsupported",
    labelKey: "local_only",
    detailKey: "unsupported_content_type",
    actionKey: "sync",
    tone: "neutral",
    retryable: false,
    actionable: false,
    busy: false,
    localOnly: true,
  });

  const recoverable = getSavedSyncPresentation({
    sync_status: "unsupported",
    error_code: "unsupported_adapter_missing",
  });
  assert.equal(recoverable.labelKey, "upgrade_required");
  assert.equal(recoverable.detailKey, "unsupported_adapter_missing");
  assert.equal(recoverable.retryable, true);
  assert.equal(recoverable.actionable, true);
  assert.equal(isSavedSyncEligibleStatus("unsupported", "unsupported_adapter_missing"), true);

  const busy = getSavedSyncPresentation({ sync_status: "pending", sync_task_id: "task-1" });
  assert.equal(busy.busy, true);
  assert.equal(busy.actionKey, "syncing");
  assert.equal(busy.actionable, false);
  assert.equal(getSavedSyncPresentation({ sync_status: "future_status" }).status, "failed");
  assert.equal(Object.hasOwn(localOnly, "label"), false);
  assert.equal(Object.hasOwn(localOnly, "detail"), false);
});

test("terminal-state detection accepts every terminal backend status and rejects active rows", () => {
  const terminalStatuses = [
    "synced",
    "already_synced",
    "login_required",
    "unsupported",
    "rate_limited",
    "extension_required",
    "failed",
  ];
  const task = { items: terminalStatuses.map((status) => ({ status })) };

  assert.equal(taskIsTerminal(task), true);
  assert.equal(isSavedTaskTerminal(task), true);
  assert.equal(taskIsTerminal({ items: [{ status: "pending" }] }), false);
  assert.equal(taskIsTerminal({ items: [{ status: "syncing" }] }), false);
});

test("strict API and normalization preserve both desktop request shapes", async () => {
  const requests = [];
  const api = createStrictSavedApi(async (path, options) => {
    requests.push({ path, options });
    return { ok: true };
  });
  const normalized = normalizeSavedItem({
    url: "https://youtu.be/video-1",
    content_id: "video-1",
    up_name: "author",
  });

  assert.equal(normalized.item_key, "youtube:video-1");
  await api.save("favorite", normalized);
  await api.remove("favorite", normalized.item_key);
  await api.status("favorite", normalized.item_key);
  await api.list("favorite", 25, 5);
  await api.sync("favorite", [normalized.item_key]);
  await api.pollTask("task/1");

  assert.deepEqual(
    requests.map((request) => request.path),
    [
      "/saved/favorite",
      "/saved/favorite/remove",
      "/saved/favorite/status?item_key=youtube%3Avideo-1",
      "/saved/favorite?limit=25&offset=5",
      "/saved/favorite/sync",
      "/saved-sync/tasks/task%2F1",
    ],
  );
  assert.equal(JSON.parse(requests[0].options.body).author_name, "author");
  assert.throws(() => api.list("unknown"), /Unknown saved list/);
});

test("batch button state is DOM-injected and the ES module installs its classic namespace", () => {
  const attributes = new Map([["aria-busy", "true"]]);
  const button = {
    disabled: false,
    setAttribute(name, value) {
      attributes.set(name, value);
    },
    removeAttribute(name) {
      attributes.delete(name);
    },
  };

  updateSavedBatchButtonState(button, 0);

  assert.equal(button.disabled, true);
  assert.equal(attributes.get("aria-disabled"), "true");
  assert.equal(attributes.has("aria-busy"), false);
  assert.equal(typeof globalThis.OBCSavedSyncCore?.createSavedSubmissionFence, "function");
});
