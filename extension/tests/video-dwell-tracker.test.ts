/**
 * Tests for the VideoDwellTracker — pure dwell session lifecycle that
 * the kernel drives from navigation observers. No DOM needed.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { VideoDwellTracker } from "../src/content/video-dwell-tracker.ts";
import type { BehaviorEvent } from "../src/shared/types.ts";

interface Harness {
  clock: { ms: number };
  emitted: BehaviorEvent[];
  tracker: VideoDwellTracker;
}

function makeHarness(): Harness {
  const clock = { ms: 0 };
  const emitted: BehaviorEvent[] = [];
  const tracker = new VideoDwellTracker({
    now: () => clock.ms,
    emit: (event) => emitted.push(event),
    buildEvent: (previousUrl, metadata) => ({
      type: "click",
      url: previousUrl,
      title: "",
      timestamp: clock.ms,
      source_platform: "bilibili",
      context: {
        pageType: "video",
        viewport: { width: 1440, height: 900 },
        scrollPosition: 0,
      },
      metadata,
    }),
  });
  return { clock, emitted, tracker };
}

test("flush after 18s on a 60s video records watch_seconds=18 and duration", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVdeep", 60);
  clock.ms = 18_000;
  const ev = tracker.flush("navigation:pushState");

  assert.notEqual(ev, null);
  assert.equal(emitted.length, 1);
  assert.equal(emitted[0].type, "click");
  assert.equal(emitted[0].url, "https://www.bilibili.com/video/BVdeep");
  assert.equal(emitted[0].metadata.watch_seconds, 18);
  assert.equal(emitted[0].metadata.video_duration_seconds, 60);
  assert.equal(emitted[0].metadata.dwell_source, "video_page_exit");
});

test("quick-exit after 2s records watch_seconds=2", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVquick", 600);
  clock.ms = 2_000;
  tracker.flush("navigation:popstate");

  assert.equal(emitted.length, 1);
  assert.equal(emitted[0].metadata.watch_seconds, 2);
  assert.equal(emitted[0].metadata.video_duration_seconds, 600);
});

test("flush omits video_duration_seconds when duration is unknown", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVnoDur", null);
  clock.ms = 12_000;
  tracker.flush("navigation:pushState");

  assert.equal(emitted.length, 1);
  assert.equal(emitted[0].metadata.watch_seconds, 12);
  assert.equal(
    "video_duration_seconds" in emitted[0].metadata,
    false,
    "no video duration when unknown",
  );
});

test("updateDuration backfills duration learned from the <video> element mid-session", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVlazy", null);
  clock.ms = 1_000;
  tracker.updateDuration(420);
  clock.ms = 30_000;
  tracker.flush("pagehide");

  assert.equal(emitted.length, 1);
  assert.equal(emitted[0].metadata.video_duration_seconds, 420);
  assert.equal(emitted[0].metadata.watch_seconds, 30);
});

test("flush with no active session is a no-op", () => {
  const { tracker, emitted } = makeHarness();
  const ev = tracker.flush("pagehide");
  assert.equal(ev, null);
  assert.equal(emitted.length, 0);
});

test("consecutive enters on different URLs auto-flush the prior session", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVfirst", 100);
  clock.ms = 5_000;
  tracker.enter("https://www.bilibili.com/video/BVsecond", 200);

  assert.equal(emitted.length, 1, "prior session flushed on second enter");
  assert.equal(emitted[0].url, "https://www.bilibili.com/video/BVfirst");
  assert.equal(emitted[0].metadata.watch_seconds, 5);
  assert.equal(emitted[0].metadata.dwell_reason, "interrupted");
  assert.equal(tracker.hasActiveSession(), true);
});

test("re-entering the same URL does NOT auto-flush (refresh / replaceState same page)", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVsame", 60);
  clock.ms = 3_000;
  // Same URL — likely a redundant replaceState; do not flush.
  tracker.enter("https://www.bilibili.com/video/BVsame", 60);
  assert.equal(emitted.length, 0);
});
