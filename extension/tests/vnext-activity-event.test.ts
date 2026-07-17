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
    id: "f2da806b-16c0-82b5-b9d8-d55b492cbd85",
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

test("activity IDs are stable across retries and secret URL parameters are removed", () => {
  const behavior = event("click");
  behavior.url = "https://www.xiaohongshu.com/explore/n1?xsec_token=secret&source=feed&access_token=also-secret";
  behavior.source_platform = "xhs";
  behavior.metadata = {
    note_id: "n1",
    xsec_token: "metadata-secret",
    detail_url: "https://www.xiaohongshu.com/explore/n1?xsec_token=nested&source=feed",
  };

  const first = normalizeActivityEvent(behavior);
  const retried = normalizeActivityEvent(structuredClone(behavior));

  assert.equal(first?.id, retried?.id);
  assert.match(first?.id ?? "", /^[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(first?.url, "https://www.xiaohongshu.com/explore/n1?source=feed");
  assert.deepEqual(first?.metadata, {
    note_id: "n1",
    detail_url: "https://www.xiaohongshu.com/explore/n1?source=feed",
    page_type: "video",
    scroll_position: 10,
    viewport: { width: 1280, height: 720 },
  });
  assert.equal(JSON.stringify(first).includes("secret"), false);
});

test("outbound URLs fail closed for userinfo, token fragments, and malformed HTTP URLs", () => {
  const withSecrets = event("view");
  withSecrets.url = " \thttps://user:password@example.com/item?page=2&auth=query-auth&credentials=query-credentials&X-Amz-Signature=query-secret#access_token=fragment-secret&signatures=fragment-signatures&X-Goog-Signature=fragment-signature&state=ok";
  assert.equal(normalizeActivityEvent(withSecrets)?.url, "https://example.com/item?page=2#state=ok");
  const malformed = event("view");
  malformed.url = "https://[broken?access_token=secret";
  assert.equal(normalizeActivityEvent(malformed)?.url, null);
});

test("legacy source aliases and action names map to closed vNext values", () => {
  assert.equal(normalizeActivityEvent(event("click"))?.kind, "view");
  assert.equal(normalizeActivityEvent({ ...event("like"), source_platform: "xhs" })?.source_id, "xiaohongshu");
  assert.equal(normalizeActivityEvent({ ...event("share"), source_platform: "dy" }), null);
});

test("pause currentTime becomes consumed watch duration instead of video length", () => {
  const pause = event("pause");
  pause.metadata = { bvid: "BV1abc", currentTime: 15.5, duration: 600 };

  const normalized = normalizeActivityEvent(pause);

  assert.equal(normalized?.kind, "dwell");
  assert.equal(normalized?.duration_seconds, 15.5);
  assert.equal("currentTime" in (normalized?.metadata ?? {}), false);
  assert.equal(normalized?.metadata.duration, 600);
});

test("activity normalization bounds strings to the backend contract before enqueue", () => {
  const oversized = event("view");
  oversized.title = "🙂".repeat(1_001);
  oversized.url = `https://example.com/item?value=${"x".repeat(2_100)}`;
  oversized.metadata = { post_id: "e".repeat(501) };

  const normalized = normalizeActivityEvent(oversized);

  assert.equal(normalized?.title, "🙂".repeat(1_000));
  assert.equal(normalized?.content_external_id, "e".repeat(500));
  assert.equal(normalized?.url, null);
});
