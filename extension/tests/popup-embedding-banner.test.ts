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
  const initialized = { initialized: true };
  assert.equal(shouldShowEmbeddingBanner({ embedding_ready: false }, initialized), true);
  assert.equal(shouldShowEmbeddingBanner({ embedding_ready: true }, initialized), false);
  // backend unreachable / older backend without the field → stay silent
  assert.equal(shouldShowEmbeddingBanner(null, initialized), false);
  assert.equal(shouldShowEmbeddingBanner(undefined, initialized), false);
  assert.equal(shouldShowEmbeddingBanner({}, initialized), false);
});

test("shouldShowEmbeddingBanner waits for a healthy, initialized backend", () => {
  const off = { embedding_ready: false };
  // Degraded backend: the only real task is repairing the LLM config.
  assert.equal(
    shouldShowEmbeddingBanner({ ...off, status: "degraded" }, { initialized: true }),
    false,
  );
  // Pre-init: the init checklist owns embedding messaging; no feed to dedup yet.
  assert.equal(shouldShowEmbeddingBanner(off, { initialized: false }), false);
  // No runtime snapshot yet → stay silent until the next poll classifies.
  assert.equal(shouldShowEmbeddingBanner(off), false);
  assert.equal(shouldShowEmbeddingBanner(off, null), false);
  // Healthy + initialized + embedding off → the banner's actual moment.
  assert.equal(shouldShowEmbeddingBanner({ ...off, status: "ok" }, { initialized: true }), true);
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
