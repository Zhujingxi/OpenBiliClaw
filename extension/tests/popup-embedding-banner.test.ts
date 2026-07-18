import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  installEmbeddingBannerAutoRefresh,
  shouldShowEmbeddingBanner,
} from "../popup/popup-embedding-banner.js";

const popupHtml = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "popup", "popup.html"),
  "utf8",
);

test("shouldShowEmbeddingBanner nags only when backend explicitly reports embedding off", () => {
  assert.equal(shouldShowEmbeddingBanner({ embedding_ready: false }), true);
  assert.equal(shouldShowEmbeddingBanner({ embedding_ready: true }), false);
  // backend unreachable / older backend without the field → stay silent
  assert.equal(shouldShowEmbeddingBanner(null), false);
  assert.equal(shouldShowEmbeddingBanner(undefined), false);
  assert.equal(shouldShowEmbeddingBanner({}), false);
});

function fakeHost(visibilityState = "visible") {
  const handlers = new Map<string, Array<() => void>>();
  return {
    visibilityState,
    addEventListener(type: string, fn: () => void) {
      const list = handlers.get(type) ?? [];
      list.push(fn);
      handlers.set(type, list);
    },
    removeEventListener(type: string, fn: () => void) {
      handlers.set(type, (handlers.get(type) ?? []).filter((h) => h !== fn));
    },
    dispatch(type: string) {
      for (const fn of handlers.get(type) ?? []) fn();
    },
    count(type: string) {
      return (handlers.get(type) ?? []).length;
    },
  };
}

test("auto-refresh re-runs the check on visibilitychange (visible) and on focus", () => {
  const doc = fakeHost("visible");
  const win = fakeHost("visible");
  let calls = 0;
  installEmbeddingBannerAutoRefresh(() => {
    calls += 1;
  }, { doc: doc as never, win: win as never });

  doc.dispatch("visibilitychange");
  win.dispatch("focus");

  assert.equal(calls, 2);
});

test("auto-refresh does not re-run while the panel is hidden", () => {
  const doc = fakeHost("hidden");
  const win = fakeHost("hidden");
  let calls = 0;
  installEmbeddingBannerAutoRefresh(() => {
    calls += 1;
  }, { doc: doc as never, win: win as never });

  doc.dispatch("visibilitychange");

  assert.equal(calls, 0);
});

test("auto-refresh teardown removes both listeners", () => {
  const doc = fakeHost("visible");
  const win = fakeHost("visible");
  const teardown = installEmbeddingBannerAutoRefresh(() => {}, {
    doc: doc as never,
    win: win as never,
  });

  assert.equal(doc.count("visibilitychange"), 1);
  assert.equal(win.count("focus"), 1);

  teardown();

  assert.equal(doc.count("visibilitychange"), 0);
  assert.equal(win.count("focus"), 0);
});

test("popup has a global [hidden] reset so el.hidden actually hides", () => {
  // Nearly every layout class sets `display: flex/grid`, which beats the UA
  // `[hidden] { display: none }` rule at equal specificity. Without a global
  // `!important` reset, `el.hidden = true` is a no-op for the embedding
  // banner, the 20 comment composers, the profile-edit panel, toasts, etc.
  // (the embedding banner shipped permanently visible because of exactly this).
  assert.match(popupHtml, /\[hidden\]\s*\{\s*display:\s*none\s*!important/);
});
