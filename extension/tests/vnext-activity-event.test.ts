import assert from "node:assert/strict";
import test from "node:test";

import { normalizeActivityEvent } from "../src/shared/activity-event.ts";
import type { BehaviorEvent } from "../src/shared/types.ts";

function event(type: string): BehaviorEvent {
  return {
    type,
    url: "https://www.bilibili.com/video/BV1abc",
    title: "A useful video",
    timestamp: Date.UTC(2026, 6, 17, 10, 0, 0),
    source_platform: "bili",
    context: { pageType: "video", viewport: { width: 1280, height: 720 }, scrollPosition: 10 },
    metadata: { bvid: "BV1abc", watch_seconds: 42 },
  };
}

test("passive behavior is normalized to one canonical ActivityEvent", () => {
  assert.deepEqual(normalizeActivityEvent(event("favorite")), {
    source_id: "bilibili",
    kind: "favorite",
    occurred_at: "2026-07-17T10:00:00.000Z",
    content_external_id: "BV1abc",
    url: "https://www.bilibili.com/video/BV1abc",
    title: "A useful video",
    duration_seconds: 42,
    metadata: {
      bvid: "BV1abc",
      page_type: "video",
      scroll_position: 10,
      viewport: { width: 1280, height: 720 },
    },
  });
});

test("legacy source aliases and action names map to closed vNext values", () => {
  assert.equal(normalizeActivityEvent(event("click"))?.kind, "view");
  assert.equal(normalizeActivityEvent({ ...event("like"), source_platform: "xhs" })?.source_id, "xiaohongshu");
  assert.equal(normalizeActivityEvent({ ...event("share"), source_platform: "dy" }), null);
});
