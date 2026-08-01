import test from "node:test";
import assert from "node:assert/strict";

import {
  BADGE_COLOR_PENDING,
  BADGE_COLOR_UNINITIALIZED,
  BADGE_COLOR_UNREACHABLE,
  BADGE_TITLE_DEFAULT,
  BADGE_TITLE_PENDING,
  BADGE_TITLE_UNINITIALIZED,
  BADGE_TITLE_UNREACHABLE,
  computeActionBadge,
  createPendingBadgeRefreshScheduler,
  flushResponseReportsUninitialized,
} from "../src/background/badge.ts";

test("unreachable backend renders the gray daemon hint", () => {
  const view = computeActionBadge(false, false);
  assert.equal(view.text, "!");
  assert.equal(view.color, BADGE_COLOR_UNREACHABLE);
  assert.equal(view.title, BADGE_TITLE_UNREACHABLE);
});

test("unreachable wins over a stale uninitialized flag", () => {
  const view = computeActionBadge(false, true);
  assert.equal(view.color, BADGE_COLOR_UNREACHABLE);
  assert.equal(view.title, BADGE_TITLE_UNREACHABLE);
});

test("reachable but uninitialized renders the orange guided-init hint", () => {
  const view = computeActionBadge(true, true);
  assert.equal(view.text, "!");
  assert.equal(view.color, BADGE_COLOR_UNINITIALIZED);
  assert.equal(view.title, BADGE_TITLE_UNINITIALIZED);
});

test("reachable and initialized clears the badge", () => {
  const view = computeActionBadge(true, false);
  assert.equal(view.text, "");
  assert.equal(view.title, BADGE_TITLE_DEFAULT);
});

test("unknown reachability with no init signal stays clear", () => {
  // Before the first probe completes nothing has been established — an empty
  // badge must not flash "!" during startup.
  const view = computeActionBadge(null, false);
  assert.equal(view.text, "");
  assert.equal(view.title, BADGE_TITLE_DEFAULT);
});

test("healthy initialized backend renders the pending confirmation count", () => {
  const view = computeActionBadge(true, false, 3);
  assert.equal(view.text, "3");
  assert.equal(view.color, BADGE_COLOR_PENDING);
  assert.equal(view.title, BADGE_TITLE_PENDING(3));
});

test("health and initialization badges take priority over pending confirmations", () => {
  assert.equal(computeActionBadge(false, false, 7).title, BADGE_TITLE_UNREACHABLE);
  assert.equal(computeActionBadge(true, true, 7).title, BADGE_TITLE_UNINITIALIZED);
  assert.equal(computeActionBadge(null, false, 7).title, BADGE_TITLE_DEFAULT);
});

test("pending confirmation badge is clamped and ignores invalid counts", () => {
  assert.equal(computeActionBadge(true, false, 120).text, "99+");
  assert.equal(computeActionBadge(true, false, -1).text, "");
  assert.equal(computeActionBadge(true, false, Number.NaN).text, "");
});

test("runtime-triggered pending refreshes debounce into one request", async () => {
  const callbacks: Array<() => void> = [];
  const cleared: number[] = [];
  let refreshes = 0;
  const scheduler = createPendingBadgeRefreshScheduler(
    async () => {
      refreshes += 1;
    },
    {
      delayMs: 250,
      setTimer(callback) {
        callbacks.push(callback);
        return callbacks.length;
      },
      clearTimer(timerId) {
        cleared.push(Number(timerId));
      },
    },
  );

  scheduler.schedule();
  scheduler.schedule();
  scheduler.schedule();
  assert.deepEqual(cleared, [1, 2]);
  await callbacks.at(-1)?.();
  assert.equal(refreshes, 1);
});

test("flush response with all events rejected as not_initialized is detected", () => {
  assert.equal(
    flushResponseReportsUninitialized({
      accepted: 0,
      rejected: [
        { index: 0, type: "play", reason: "not_initialized" },
        { index: 1, type: "like", reason: "not_initialized" },
      ],
    }),
    true,
  );
});

test("flush response with accepted events is not flagged", () => {
  assert.equal(
    flushResponseReportsUninitialized({ accepted: 3, rejected: [] }),
    false,
  );
});

test("flush response rejected for other reasons is not flagged", () => {
  assert.equal(
    flushResponseReportsUninitialized({
      accepted: 0,
      rejected: [{ index: 0, type: "play", reason: "invalid_payload" }],
    }),
    false,
  );
});

test("malformed flush payloads are not flagged", () => {
  assert.equal(flushResponseReportsUninitialized(null), false);
  assert.equal(flushResponseReportsUninitialized("ok"), false);
  assert.equal(flushResponseReportsUninitialized({ rejected: "not_initialized" }), false);
});
