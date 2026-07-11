import test from "node:test";
import assert from "node:assert/strict";

import {
  normalizeDelightCandidate,
  normalizeRecommendation,
} from "../popup/popup-helpers.js";
import {
  captureSavedFocus as capturePopupSavedFocus,
  createRetainedSavedListState as createPopupRetainedState,
  createSavedSyncTaskTracker,
  normalizeCanonicalSavedItem,
  partitionSavedQueueResults,
  restoreSavedFocus as restorePopupSavedFocus,
} from "../popup/popup-saved-sync.js";
import { normalizeSavedItemInput } from "../popup/popup-api.js";
import {
  createDurableTaskTracker as createMobileTaskTracker,
  createRetainedSavedListState,
  createSavedMutationRegistry,
  captureSavedFocus,
  createDialogFocusController,
  restoreSavedFocus,
} from "../../src/openbiliclaw/web/js/saved-sync-runtime.js";
import {
  normalizeDelightCandidate as normalizeMobileDelight,
  normalizeRecommendation as normalizeMobileRecommendation,
  normalizeSavedIdentity as normalizeMobileSavedIdentity,
} from "../../src/openbiliclaw/web/js/view-models.js";

const identities = [
  {
    item_key: "youtube:yt-1",
    source_platform: "youtube",
    content_id: "yt-1",
    content_url: "https://www.youtube.com/watch?v=yt-1",
    content_type: "video",
  },
  {
    item_key: "twitter:1900000000000000001",
    source_platform: "twitter",
    content_id: "1900000000000000001",
    content_url: "https://x.com/openai/status/1900000000000000001",
    content_type: "tweet",
  },
  {
    item_key: "zhihu:answer-42",
    source_platform: "zhihu",
    content_id: "answer-42",
    content_url: "https://www.zhihu.com/question/1/answer/42",
    content_type: "answer",
  },
  {
    item_key: "web:url:0123456789abcdef01234567",
    source_platform: "web",
    content_id: "",
    content_url: "https://example.com/articles/local-first",
    content_type: "article",
  },
];

test("recommendation and delight normalizers preserve canonical saved identity", () => {
  for (const identity of identities) {
    const raw = { ...identity, id: 99, bvid: "", title: "demo" };
    for (const normalized of [
      normalizeRecommendation(raw),
      normalizeDelightCandidate(raw),
      normalizeMobileRecommendation(raw),
      normalizeMobileDelight(raw),
    ]) {
      assert.deepEqual(
        {
          item_key: normalized.item_key,
          source_platform: normalized.source_platform,
          content_id: normalized.content_id,
          content_url: normalized.content_url,
          content_type: normalized.content_type,
        },
        identity,
      );
    }
  }

  const namespacedText = {
    item_key: "twitter:1900000000000000001",
    source_platform: "twitter",
    bvid: "twitter:1900000000000000001",
    content_url: "https://x.com/openai/status/1900000000000000001",
  };
  for (const normalized of [
    normalizeRecommendation(namespacedText),
    normalizeMobileRecommendation(namespacedText),
  ]) {
    assert.equal(normalized.content_id, "");
    assert.equal(normalized.content_type, "");
  }
});

test("saved identity never uses a recommendation row id or namespaced legacy id as content_id", () => {
  assert.deepEqual(normalizeCanonicalSavedItem({
    id: 77,
    bvid: "youtube:yt-1",
    item_key: "youtube:yt-1",
    source_platform: "youtube",
    content_url: "https://www.youtube.com/watch?v=yt-1",
    content_type: "video",
  }), {
    item_key: "youtube:yt-1",
    source_platform: "youtube",
    content_id: "",
    content_url: "https://www.youtube.com/watch?v=yt-1",
    content_type: "video",
  });

  assert.equal(normalizeCanonicalSavedItem({
    id: 88,
    item_key: "web:url:0123456789abcdef01234567",
    source_platform: "web",
    content_url: "https://example.com/story",
    content_type: "article",
  }).content_id, "");

  assert.deepEqual(normalizeMobileSavedIdentity({
    id: 99,
    bvid: "twitter:1900000000000000001",
    item_key: "twitter:1900000000000000001",
    source_platform: "twitter",
    content_url: "https://x.com/openai/status/1900000000000000001",
    content_type: "tweet",
  }), {
    id: 99,
    bvid: "twitter:1900000000000000001",
    item_key: "twitter:1900000000000000001",
    source_platform: "twitter",
    content_id: "",
    content_url: "https://x.com/openai/status/1900000000000000001",
    content_type: "tweet",
  });
});

test("save payload normalization does not force unknown text content to video", () => {
  assert.equal(normalizeSavedItemInput({
    item_key: "twitter:url:0123456789abcdef01234567",
    source_platform: "twitter",
    content_id: "",
    content_url: "https://x.com/openai/status/1900000000000000001",
    content_type: "",
  }).content_type, "");
  assert.equal(normalizeSavedItemInput({
    id: 77,
    bvid: "youtube:yt-1",
    source_platform: "youtube",
    content_url: "https://www.youtube.com/watch?v=yt-1",
  }).content_id, "");
});

test("desktop saved API uses bounded strict requests and propagates failures", async () => {
  delete (globalThis as any).OpenBiliClawSavedSync;
  await import("../../src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js");
  const core = (globalThis as any).OpenBiliClawSavedSync;
  const calls: Array<{ path: string; options: Record<string, unknown> }> = [];
  const api = core.createStrictSavedApi(async (path: string, options = {}) => {
    calls.push({ path, options });
    if (path.includes("/status")) throw new Error("HTTP 503");
    return { ok: true };
  });

  const xItem = {
    id: 9,
    item_key: "twitter:1900000000000000001",
    source_platform: "twitter",
    content_id: "1900000000000000001",
    content_url: "https://x.com/openai/status/1900000000000000001",
    content_type: "tweet",
  };
  assert.deepEqual(core.normalizeSavedItem(xItem), xItem);
  await api.save("favorite", xItem);
  await api.remove("favorite", xItem.item_key);
  await api.list("favorite");
  await api.sync("favorite", [xItem.item_key]);
  await api.pollTask("123e4567-e89b-12d3-a456-426614174000");
  await assert.rejects(api.status("favorite", xItem.item_key), /HTTP 503/);

  assert.equal(calls.length, 6);
  for (const call of calls) {
    assert.equal(typeof call.options.timeoutMs, "number");
    assert.ok(Number(call.options.timeoutMs) > 0);
    assert.ok(Number(call.options.timeoutMs) <= 15_000);
  }
});

test("durable task tracker keeps nonterminal tasks resumable beyond the foreground horizon", async () => {
  const core = (globalThis as any).OpenBiliClawSavedSync;
  let now = 0;
  let visible = true;
  const scheduled: Array<{ run: () => void; delay: number }> = [];
  const snapshots = [
    { task_id: "task-1", items: [{ item_key: "youtube:1", status: "syncing" }] },
    { task_id: "task-1", items: [{ item_key: "youtube:1", status: "syncing" }] },
    { task_id: "task-1", items: [{ item_key: "youtube:1", status: "synced" }] },
  ];
  const events: string[] = [];
  const tracker = core.createDurableTaskTracker({
    poll: async () => snapshots.shift(),
    now: () => now,
    isVisible: () => visible,
    schedule: (run: () => void, delay: number) => {
      scheduled.push({ run, delay });
      return scheduled.length;
    },
    cancel: () => {},
    foregroundHorizonMs: 20_000,
    visibleDelayMs: 500,
    hiddenDelayMs: 5_000,
  });

  tracker.track(snapshots.shift(), {
    onProgress: () => events.push("progress"),
    onBackground: () => events.push("仍在后台同步"),
    onTerminal: () => events.push("terminal"),
  });
  assert.equal(scheduled[0].delay, 500);
  now = 21_000;
  visible = false;
  await scheduled.shift().run();
  assert.ok(events.includes("仍在后台同步"));
  assert.equal(tracker.has("task-1"), true);
  assert.equal(scheduled[0].delay, 5_000);
  visible = true;
  await scheduled.shift().run();
  assert.ok(events.includes("terminal"));
  assert.equal(tracker.has("task-1"), false);
});

test("saved list state retains the last successful rows when refresh fails", () => {
  const state = createRetainedSavedListState();
  state.commit({ items: [{ item_key: "youtube:1" }], total: 1 });
  state.fail(new Error("offline"));
  assert.deepEqual(state.snapshot(), {
    items: [{ item_key: "youtube:1" }],
    total: 1,
    loaded: true,
    error: "offline",
  });
  state.commit({ items: [{ item_key: "twitter:2" }], total: 1 });
  assert.equal(state.snapshot().error, "");
  assert.equal(state.snapshot().items[0].item_key, "twitter:2");
});

test("saved mutation registry isolates keys and discards stale hydration", async () => {
  const registry = createSavedMutationRegistry();
  let finishHydration: (value: unknown) => void = () => {};
  const hydration = registry.hydrate("favorite", "youtube:1", () => new Promise((resolve) => {
    finishHydration = resolve;
  }));
  let finishSave: (value: unknown) => void = () => {};
  const mutation = registry.toggle("favorite", "youtube:1", {
    add: () => new Promise((resolve) => { finishSave = resolve; }),
    remove: async () => ({ saved: false }),
  });
  assert.equal(registry.isBusy("favorite", "youtube:1"), true);
  assert.equal(registry.isBusy("favorite", "twitter:2"), false);
  assert.equal(registry.isSaved("favorite", "youtube:1"), true);
  finishSave({ saved: true });
  await mutation;
  finishHydration({ saved: false });
  await hydration;
  assert.equal(registry.isSaved("favorite", "youtube:1"), true);
});

test("mobile task tracker survives an aborted poll and remains resumable", async () => {
  const scheduled: Array<() => void> = [];
  let attempts = 0;
  const tracker = createMobileTaskTracker({
    poll: async () => {
      attempts += 1;
      if (attempts === 1) throw Object.assign(new Error("aborted"), { name: "AbortError" });
      return { task_id: "task-mobile", items: [{ item_key: "youtube:1", status: "synced" }] };
    },
    schedule: (run: () => void) => { scheduled.push(run); return scheduled.length; },
    cancel: () => {},
  });
  tracker.track({
    task_id: "task-mobile",
    items: [{ item_key: "youtube:1", status: "syncing" }],
  });
  await scheduled.shift()();
  assert.equal(tracker.has("task-mobile"), true);
  assert.equal(tracker.resume("task-mobile"), true);
  await scheduled.pop()();
  assert.equal(tracker.has("task-mobile"), false);
});

test("saved focus token restores the same item action after rerender", () => {
  let focused = 0;
  const action = {
    dataset: { savedAction: "remove" },
    focus() { focused += 1; },
    closest(selector: string) { return selector === "[data-item-key]" ? card : null; },
  };
  const card = {
    dataset: { itemKey: "youtube:video-1" },
    querySelectorAll() { return [action]; },
  };
  const root = { querySelectorAll() { return [card]; } };
  const token = captureSavedFocus(root, action);
  assert.deepEqual(token, { itemKey: "youtube:video-1", action: "remove" });
  assert.equal(restoreSavedFocus(root, token), true);
  assert.equal(focused, 1);
});

test("extension saved runtime retains list state and keeps polling after the horizon", async () => {
  const retained = createPopupRetainedState();
  retained.commit({ items: [{ item_key: "zhihu:1" }], total: 1 });
  retained.fail("offline");
  assert.equal(retained.snapshot().items[0].item_key, "zhihu:1");

  let now = 0;
  const scheduled: Array<() => void> = [];
  const messages: string[] = [];
  const tracker = createSavedSyncTaskTracker({
    poll: async () => ({ task_id: "popup-task", items: [{ item_key: "zhihu:1", status: "syncing" }] }),
    now: () => now,
    schedule: (run: () => void) => { scheduled.push(run); return scheduled.length; },
    cancel: () => {},
    foregroundHorizonMs: 20_000,
  });
  tracker.track({ task_id: "popup-task", items: [{ item_key: "zhihu:1", status: "syncing" }] }, {
    onBackground: () => messages.push("仍在后台同步"),
  });
  now = 21_000;
  await scheduled.shift()();
  assert.deepEqual(messages, ["仍在后台同步"]);
  assert.equal(tracker.has("popup-task"), true);

  const action = { dataset: { savedAction: "sync" }, focus() {}, closest: () => card };
  const card = { dataset: { itemKey: "zhihu:1" }, querySelectorAll: () => [action] };
  const root = { querySelectorAll: () => [card] };
  assert.equal(restorePopupSavedFocus(root, capturePopupSavedFocus(root, action)), true);
});

test("desktop saved core retains rows, isolates mutations, and restores focus", async () => {
  const core = (globalThis as any).OpenBiliClawSavedSync;
  const retained = core.createRetainedSavedListState();
  retained.commit({ items: [{ item_key: "reddit:t3_1" }], total: 1 });
  retained.fail("offline");
  assert.equal(retained.snapshot().items[0].item_key, "reddit:t3_1");

  const registry = core.createSavedMutationRegistry();
  const first = registry.toggle("favorite", "reddit:t3_1", {
    add: async () => ({ saved: true }),
    remove: async () => ({ saved: false }),
  });
  assert.equal(registry.isBusy("favorite", "reddit:t3_1"), true);
  assert.equal(registry.isBusy("favorite", "youtube:2"), false);
  await first;

  let focused = false;
  const action = { dataset: { savedAction: "sync" }, focus() { focused = true; }, closest: () => card };
  const card = { dataset: { itemKey: "reddit:t3_1" }, querySelectorAll: () => [action] };
  const root = { querySelectorAll: () => [card] };
  const token = core.captureSavedFocus(root, action);
  assert.equal(core.restoreSavedFocus(root, token), true);
  assert.equal(focused, true);
});

test("dialog focus controller closes on Escape and restores its opener", () => {
  const listeners = new Map<string, (event: any) => void>();
  let openerFocused = 0;
  let closed = 0;
  const opener = { focus() { openerFocused += 1; } };
  const first = { focus() {} };
  const last = { focus() {} };
  const doc = {
    activeElement: first,
    addEventListener(type: string, fn: (event: any) => void) { listeners.set(type, fn); },
    removeEventListener(type: string) { listeners.delete(type); },
  };
  const dialog = {
    contains: () => true,
    querySelectorAll: () => [first, last],
  };
  const controller = createDialogFocusController({
    dialog,
    opener,
    document: doc,
    onClose: () => { closed += 1; },
  });
  controller.activate();
  listeners.get("keydown")?.({ key: "Escape", preventDefault() {} });
  assert.equal(closed, 1);
  controller.deactivate();
  assert.equal(openerFocused, 1);
});

test("extension all-queue keeps failed URL items and accepts server-issued item keys", () => {
  const urlItem = {
    item_key: "",
    source_platform: "web",
    content_id: "",
    content_url: "https://example.com/story",
    content_type: "article",
  };
  const failed = { ...urlItem, content_url: "https://example.com/failed" };
  const partition = partitionSavedQueueResults([urlItem, failed], [
    { status: "fulfilled", value: { saved: true, item_key: "web:url:0123456789abcdef01234567" } },
    { status: "rejected", reason: new Error("offline") },
  ]);
  assert.equal(partition.savedCount, 1);
  assert.equal(partition.failedCount, 1);
  assert.equal(partition.saved[0].itemKey, "web:url:0123456789abcdef01234567");
  assert.deepEqual(partition.remaining, [failed]);
});
