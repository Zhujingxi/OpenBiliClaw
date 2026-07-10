import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const kernelSource = readFileSync(
  new URL("../src/content/kernel.ts", import.meta.url),
  "utf8",
);

test("collector observes clicks in capture phase so stopped platform events are still captured", () => {
  assert.match(
    kernelSource,
    /document\.addEventListener\("click",\s*\(event\) => \{[\s\S]*?\},\s*\{\s*capture:\s*true\s*\}\s*\);/,
  );
});

test("click path treats a pressed like/favorite/follow control as a retraction", () => {
  // Clicking an already-active control withdraws the action → emit a
  // neutral retraction feedback event instead of the positive event.
  assert.match(kernelSource, /actionHint\.pressed === true/);
  assert.match(kernelSource, /feedback_type:\s*"retraction"/);
  assert.match(kernelSource, /retracted_action:/);
});

test("click path suppresses the retraction on tap-authoritative platforms (no double-emit)", () => {
  // On X the GraphQL tap emits the authoritative retraction; the DOM path
  // must only suppress, never emit a duplicate.
  assert.match(kernelSource, /strongSignalSource === "tap"/);
});

test("video play begins a dwell segment; pause and ended end it", () => {
  assert.match(kernelSource, /addEventListener\("play",[\s\S]*?beginSegment\(\)/);
  assert.match(kernelSource, /addEventListener\("pause",[\s\S]*?endSegment\(\)/);
  assert.match(kernelSource, /addEventListener\("ended",[\s\S]*?endSegment\(\)/);
});

test("video listeners begin a segment at bind time when the element is already playing", () => {
  assert.match(kernelSource, /!video\.paused && !video\.ended[\s\S]*?beginSegment\(\)/);
});

test("late-rendered <video> is retried with a bounded, navigation-cancelled loop", () => {
  assert.match(kernelSource, /_VIDEO_ATTACH_RETRY_MS\s*=\s*500/);
  assert.match(kernelSource, /_VIDEO_ATTACH_MAX_RETRIES\s*=\s*20/);
  assert.match(kernelSource, /cancelVideoAttachRetry\(\)/);
});

test("dwell entry is generalized across tracked page types via dwellPageTypes", () => {
  assert.match(kernelSource, /enterDwellIfTrackedPage/);
  assert.match(kernelSource, /adapter\.dwellPageTypes \?\? \["video"\]/);
});

test("visibilitychange drives visible-mode dwell segments", () => {
  assert.match(kernelSource, /addEventListener\(\s*"visibilitychange"/);
  assert.match(kernelSource, /handleVisibilityChange\(document\.hidden\)/);
});

test("visible-mode entry begins a segment only when the tab is not hidden", () => {
  assert.match(kernelSource, /!document\.hidden/);
});

test("entering a tracked non-video page emits a view event with content_id", () => {
  assert.match(kernelSource, /createEvent\("view"/);
});

test("navigation to a search result page emits a URL-derived search event", () => {
  // Kernel calls the adapter's extractSearchQuery on nav to a search page,
  // routed through the shared dedup guard.
  assert.match(kernelSource, /maybeEmitUrlSearch/);
  assert.match(kernelSource, /extractSearchQuery/);
  assert.match(kernelSource, /isDuplicateSearch/);
});
