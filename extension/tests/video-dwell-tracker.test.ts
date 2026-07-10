/**
 * Tests for the VideoDwellTracker — pure dwell session lifecycle that
 * the kernel drives from navigation + video observers. No DOM needed.
 *
 * The tracker uses a segment-accumulation model: `beginSegment()` /
 * `endSegment()` bracket the time the video is actually playing, so
 * `watch_seconds` counts playing time only, while `page_dwell_seconds`
 * keeps the wall-clock total for diagnostics.
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

test("segmented: playing 10s then paused 20s → watch_seconds=10, page_dwell_seconds=30", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVseg", 100);
  tracker.beginSegment();
  clock.ms = 10_000;
  tracker.endSegment();
  clock.ms = 30_000; // 20s paused
  const ev = tracker.flush("navigation:pushState");

  assert.notEqual(ev, null);
  assert.equal(emitted.length, 1);
  assert.equal(emitted[0].metadata.watch_seconds, 10);
  assert.equal(emitted[0].metadata.page_dwell_seconds, 30);
  assert.equal(emitted[0].metadata.video_duration_seconds, 100);
  assert.equal(emitted[0].metadata.dwell_source, "video_page_exit");
});

test("segmented: multiple begin/end cycles accumulate playing time", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVmulti", 600);
  tracker.beginSegment();
  clock.ms = 5_000;
  tracker.endSegment(); // +5s
  clock.ms = 8_000;
  tracker.beginSegment();
  clock.ms = 20_000;
  tracker.endSegment(); // +12s
  clock.ms = 25_000;
  tracker.flush("pagehide");

  assert.equal(emitted[0].metadata.watch_seconds, 17);
  assert.equal(emitted[0].metadata.page_dwell_seconds, 25);
});

test("segmented: double-begin and double-end are idempotent", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVidem", 100);
  tracker.beginSegment();
  clock.ms = 4_000;
  tracker.beginSegment(); // no-op: segment already open, must NOT reset start
  clock.ms = 10_000;
  tracker.endSegment(); // +10s from the first begin
  tracker.endSegment(); // no-op
  clock.ms = 12_000;
  tracker.flush("navigation:popstate");

  assert.equal(emitted[0].metadata.watch_seconds, 10);
});

test("segmented: flush with an open segment includes the in-flight time", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVopen", 100);
  tracker.beginSegment();
  clock.ms = 15_000;
  tracker.flush("navigation:pushState"); // no endSegment first

  assert.equal(emitted[0].metadata.watch_seconds, 15);
  assert.equal(emitted[0].metadata.page_dwell_seconds, 15);
});

test("autoplay: enter + beginSegment (no prior end) accumulates from t=0", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVauto", 60);
  tracker.beginSegment(); // autoplay — no `play` event, begun at bind time
  clock.ms = 18_000;
  tracker.flush("navigation:pushState");

  assert.equal(emitted[0].metadata.watch_seconds, 18);
});

test("clamp: known duration caps watch_seconds at duration * 1.5", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVclamp", 100);
  tracker.beginSegment();
  clock.ms = 400_000; // 400s playing (impossible without lost pause events)
  tracker.flush("pagehide");

  assert.equal(emitted[0].metadata.watch_seconds, 150); // 100 * 1.5
  // Wall-clock is left untouched for diagnostics.
  assert.equal(emitted[0].metadata.page_dwell_seconds, 400);
});

test("clamp: unknown duration caps at 600 only when raw exceeds it", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVnoDurCap", null);
  tracker.beginSegment();
  clock.ms = 900_000; // 900s
  tracker.flush("pagehide");
  assert.equal(emitted[0].metadata.watch_seconds, 600);

  const h2 = makeHarness();
  h2.tracker.enter("https://www.bilibili.com/video/BVunder", null);
  h2.tracker.beginSegment();
  h2.clock.ms = 120_000; // 120s < 600 → untouched
  h2.tracker.flush("pagehide");
  assert.equal(h2.emitted[0].metadata.watch_seconds, 120);
});

test("flush omits video_duration_seconds when duration is unknown", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVnoDur", null);
  tracker.beginSegment();
  clock.ms = 12_000;
  tracker.flush("navigation:pushState");

  assert.equal(emitted[0].metadata.watch_seconds, 12);
  assert.equal("video_duration_seconds" in emitted[0].metadata, false);
});

test("updateDuration backfills duration learned from the <video> element mid-session", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVlazy", null);
  tracker.beginSegment();
  clock.ms = 1_000;
  tracker.updateDuration(420);
  clock.ms = 30_000;
  tracker.flush("pagehide");

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
  tracker.beginSegment();
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
  tracker.beginSegment();
  clock.ms = 3_000;
  tracker.enter("https://www.bilibili.com/video/BVsame", 60);
  assert.equal(emitted.length, 0);
});

// ── mode + visibility state machine (Task 4: content-page dwell) ────────

test("visible-mode flush uses content_page_exit and omits video_duration_seconds", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.zhihu.com/question/1/answer/2", 999, "visible");
  tracker.beginSegment();
  clock.ms = 40_000;
  tracker.flush("navigation:pushState");

  assert.equal(emitted[0].metadata.dwell_source, "content_page_exit");
  assert.equal(emitted[0].metadata.watch_seconds, 40);
  assert.equal("video_duration_seconds" in emitted[0].metadata, false);
});

test("state machine: entry while hidden accumulates nothing until the visible transition", () => {
  const { clock, emitted, tracker } = makeHarness();
  // Entered hidden → kernel does NOT begin a segment; visibility drives it.
  tracker.enter("https://www.xiaohongshu.com/explore/abc", null, "visible");
  clock.ms = 10_000; // 10s while hidden — no segment open
  tracker.handleVisibilityChange(false); // became visible at t=10s
  clock.ms = 15_000; // +5s visible
  tracker.flush("navigation:pushState");

  assert.equal(emitted[0].metadata.watch_seconds, 5);
  assert.equal(emitted[0].metadata.page_dwell_seconds, 15);
});

test("state machine: hidden transition ends the segment (navigation-while-hidden flushes closed)", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.reddit.com/r/x/comments/abc/", null, "visible");
  tracker.handleVisibilityChange(false); // visible at t=0 → begin
  clock.ms = 8_000;
  tracker.handleVisibilityChange(true); // hidden at t=8 → end (+8)
  clock.ms = 20_000; // 12s hidden, no accumulation
  tracker.flush("navigation:popstate");

  assert.equal(emitted[0].metadata.watch_seconds, 8);
  assert.equal(emitted[0].metadata.page_dwell_seconds, 20);
});

test("state machine: visible→hidden→visible reopens the segment and accumulates", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://x.com/user/status/1", null, "visible");
  tracker.handleVisibilityChange(false); // begin @0
  clock.ms = 5_000;
  tracker.handleVisibilityChange(true); // end (+5) @5
  clock.ms = 10_000; // hidden 5s
  tracker.handleVisibilityChange(false); // reopen @10
  clock.ms = 15_000; // +5s
  tracker.flush("pagehide");

  assert.equal(emitted[0].metadata.watch_seconds, 10);
});

test("state machine: visibility transitions never touch a playback-mode session", () => {
  const { clock, emitted, tracker } = makeHarness();
  tracker.enter("https://www.bilibili.com/video/BVplay", 100, "playback");
  tracker.beginSegment(); // play @0
  tracker.handleVisibilityChange(true); // backgrounded — playback keeps counting
  clock.ms = 30_000;
  tracker.handleVisibilityChange(false);
  clock.ms = 30_000; // (no advance) flush
  tracker.flush("pagehide");

  assert.equal(emitted[0].metadata.watch_seconds, 30);
  assert.equal(emitted[0].metadata.dwell_source, "video_page_exit");
  assert.equal(emitted[0].metadata.video_duration_seconds, 100);
});
