import test from "node:test";
import assert from "node:assert/strict";

import {
  createSavedToggleRegistry,
  isSavedSyncEligibleStatus,
} from "../popup/popup-saved-sync.js";

function fakeButton() {
  const attrs = new Map<string, string>();
  return {
    textContent: "",
    title: "",
    disabled: false,
    setAttribute(name: string, value: string) {
      attrs.set(name, value);
    },
    getAttribute(name: string) {
      return attrs.get(name) ?? null;
    },
  };
}

test("popup saved sync eligibility follows backend state and error code", () => {
  assert.equal(isSavedSyncEligibleStatus("pending", "", "task-1"), false);
  assert.equal(isSavedSyncEligibleStatus("pending", "", ""), true);
  assert.equal(isSavedSyncEligibleStatus("syncing"), false);
  assert.equal(
    isSavedSyncEligibleStatus("unsupported", "unsupported_content_type"),
    false,
  );
  assert.equal(
    isSavedSyncEligibleStatus("unsupported", "unsupported_adapter_missing"),
    true,
  );

  for (const platform of ["youtube", "twitter", "xiaohongshu", "douyin", "zhihu", "reddit"]) {
    assert.equal(
      isSavedSyncEligibleStatus(undefined, "", ""),
      true,
      `${platform} must retain a manual sync path when the backend has no terminal state`,
    );
  }
});

test("popup saved toggle registry syncs every visible button for the same bvid", async () => {
  const first = fakeButton();
  const second = fakeButton();
  const calls: string[] = [];
  const registry = createSavedToggleRegistry({
    labels: {
      checkedText: "★",
      uncheckedText: "☆",
      checkedTitle: "取消稍后再看",
      uncheckedTitle: "稍后再看",
    },
  });

  registry.registerButton("BV1SYNC", first);
  registry.registerButton("BV1SYNC", second);

  assert.equal(first.textContent, "☆");
  assert.equal(second.textContent, "☆");

  await registry.hydrateStatus("BV1SYNC", async () => ({ saved: true }));

  assert.equal(first.textContent, "★");
  assert.equal(second.textContent, "★");
  assert.equal(first.title, "取消稍后再看");
  assert.equal(second.getAttribute("aria-pressed"), "true");

  await registry.toggle("BV1SYNC", {
    add: async (bvid) => calls.push(`add:${bvid}`),
    remove: async (bvid) => calls.push(`remove:${bvid}`),
  });

  assert.deepEqual(calls, ["remove:BV1SYNC"]);
  assert.equal(first.textContent, "☆");
  assert.equal(second.textContent, "☆");
  assert.equal(second.getAttribute("aria-pressed"), "false");
});

test("popup saved toggle registry ignores stale lazy status after a user toggle", async () => {
  const button = fakeButton();
  let resolveStatus: (value: { saved: boolean }) => void = () => {};
  const staleStatus = new Promise<{ saved: boolean }>((resolve) => {
    resolveStatus = resolve;
  });
  const registry = createSavedToggleRegistry({
    labels: {
      checkedTitle: "取消收藏",
      uncheckedTitle: "收藏",
    },
  });

  registry.registerButton("BV1RACE", button);
  const hydration = registry.hydrateStatus("BV1RACE", () => staleStatus);

  await registry.toggle("BV1RACE", {
    add: async () => {},
    remove: async () => {},
  });
  resolveStatus({ saved: false });
  await hydration;

  assert.equal(button.getAttribute("aria-pressed"), "true");
  assert.equal(button.title, "取消收藏");
});

test("popup saved toggle registry ignores stale lazy status after an external saved update", async () => {
  const button = fakeButton();
  let resolveStatus: (value: { saved: boolean }) => void = () => {};
  const staleStatus = new Promise<{ saved: boolean }>((resolve) => {
    resolveStatus = resolve;
  });
  const registry = createSavedToggleRegistry({
    labels: {
      checkedTitle: "取消收藏",
      uncheckedTitle: "收藏",
    },
  });

  registry.registerButton("BV1LIST", button);
  const hydration = registry.hydrateStatus("BV1LIST", () => staleStatus);

  registry.setSaved("BV1LIST", true);
  resolveStatus({ saved: false });
  await hydration;

  assert.equal(button.getAttribute("aria-pressed"), "true");
  assert.equal(button.title, "取消收藏");
});

test("popup saved toggle registry drops detached buttons on sync and stops updating them", () => {
  const live = fakeButton();
  const detached = fakeButton() as ReturnType<typeof fakeButton> & {
    isConnected?: boolean;
  };
  const registry = createSavedToggleRegistry({
    labels: { checkedTitle: "取消收藏", uncheckedTitle: "收藏" },
  });

  registry.registerButton("BV1GC", live);
  registry.registerButton("BV1GC", detached);

  // Both buttons render their initial (unsaved) state on registration.
  assert.equal(detached.getAttribute("aria-pressed"), "false");

  // Simulate the detached button being removed from the DOM (replaceChildren).
  detached.isConnected = false;

  // Any state change runs a sync, which prunes detached entries before applying:
  // the pruned button is left at "false" instead of advancing to the saved state.
  registry.setSaved("BV1GC", true);
  assert.equal(live.getAttribute("aria-pressed"), "true");
  assert.equal(detached.getAttribute("aria-pressed"), "false");

  // A later change must not touch the already-pruned button either.
  registry.setSaved("BV1GC", false);
  assert.equal(live.getAttribute("aria-pressed"), "false");
  assert.equal(detached.getAttribute("aria-pressed"), "false");
});

test("popup saved toggle registry pruneDetached sweeps removed buttons across re-renders", () => {
  const live = fakeButton();
  const detached = fakeButton() as ReturnType<typeof fakeButton> & {
    isConnected?: boolean;
  };
  const registry = createSavedToggleRegistry({
    labels: { checkedTitle: "取消稍后再看", uncheckedTitle: "稍后再看" },
  });

  registry.registerButton("BV1SWEEP", live);
  registry.registerButton("BV1SWEEP", detached);
  detached.isConnected = false;

  registry.pruneDetached();

  // After the sweep only the live button reacts to state changes; the swept
  // button stays at its registration-time "false" instead of flipping to "true".
  registry.setSaved("BV1SWEEP", true);
  assert.equal(live.getAttribute("aria-pressed"), "true");
  assert.equal(detached.getAttribute("aria-pressed"), "false");
});

test("popup saved toggle registry keeps a confirmed toggle when a hydration started mid-write resolves stale", async () => {
  // Regression: a re-render registers a fresh button (and its status GET) AFTER
  // the optimistic toggle but while the add/remove write is still in flight. The
  // GET reads a pre-write snapshot and resolves after the write succeeds. Without
  // bumping the mutation version on write success, the captured version still
  // matches and the stale GET rolls the just-confirmed toggle back.
  const button = fakeButton();
  let resolveAdd: (value: { saved: boolean }) => void = () => {};
  const addResult = new Promise<{ saved: boolean }>((resolve) => {
    resolveAdd = resolve;
  });
  let resolveStatus: (value: { saved: boolean }) => void = () => {};
  const staleStatus = new Promise<{ saved: boolean }>((resolve) => {
    resolveStatus = resolve;
  });
  const registry = createSavedToggleRegistry({
    labels: { checkedTitle: "取消收藏", uncheckedTitle: "收藏" },
  });

  registry.registerButton("BV1MIDWRITE", button);

  // Start a toggle whose write stays pending; the synchronous optimistic update
  // runs before the first await.
  const toggling = registry.toggle("BV1MIDWRITE", {
    add: async () => addResult,
    remove: async () => ({ saved: false }),
  });
  assert.equal(button.getAttribute("aria-pressed"), "true"); // optimistic applied

  // Mid-write re-render: a hydration captures the post-optimistic version and
  // reads a stale (pre-write) snapshot.
  const hydration = registry.hydrateStatus("BV1MIDWRITE", () => staleStatus);

  // Write succeeds first, then the stale status resolves — it must not roll back.
  resolveAdd({ saved: true });
  await toggling;
  resolveStatus({ saved: false });
  await hydration;

  assert.equal(button.getAttribute("aria-pressed"), "true");
  assert.equal(button.title, "取消收藏");
});

test("popup saved toggle registry lets concurrent lazy status calls share the same version", async () => {
  const first = fakeButton();
  const second = fakeButton();
  let resolveFirst: (value: { saved: boolean }) => void = () => {};
  const firstStatus = new Promise<{ saved: boolean }>((resolve) => {
    resolveFirst = resolve;
  });
  const registry = createSavedToggleRegistry({
    labels: {
      checkedTitle: "取消稍后再看",
      uncheckedTitle: "稍后再看",
    },
  });

  registry.registerButton("BV1MULTI", first);
  registry.registerButton("BV1MULTI", second);
  const firstHydration = registry.hydrateStatus("BV1MULTI", () => firstStatus);
  const secondHydration = registry.hydrateStatus("BV1MULTI", async () => {
    throw new Error("network down");
  });

  await secondHydration;
  resolveFirst({ saved: true });
  await firstHydration;

  assert.equal(first.getAttribute("aria-pressed"), "true");
  assert.equal(second.getAttribute("aria-pressed"), "true");
});
