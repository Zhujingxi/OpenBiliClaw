import assert from "node:assert/strict";
import test from "node:test";

import { xhsObservationEvents } from "../src/background/xhs-observation-events.ts";

test("mixed Xiaohongshu note metadata and URL-only observations are all retained safely", () => {
  const events = xhsObservationEvents({
    observed_at: Date.UTC(2026, 6, 17),
    page_type: "explore",
    notes: [{
      note_id: "one",
      url: "https://www.xiaohongshu.com/explore/one?xsec_token=secret&source=feed",
      title: "One",
    }],
    urls: [
      "https://www.xiaohongshu.com/explore/one?xsec_token=secret&source=feed",
      "https://www.xiaohongshu.com/explore/two?xsec_token=other&source=feed",
    ],
  });

  assert.equal(events.length, 2);
  assert.deepEqual(events.map((event) => [event.content_external_id, event.url, event.title]), [
    ["one", "https://www.xiaohongshu.com/explore/one?source=feed", "One"],
    ["two", "https://www.xiaohongshu.com/explore/two?source=feed", null],
  ]);
  assert.equal(new Set(events.map((event) => event.id)).size, 2);
  assert.equal(JSON.stringify(events).includes("secret"), false);
  assert.equal(JSON.stringify(events).includes("other"), false);
});

test("Xiaohongshu observations obey ActivityEvent string bounds", () => {
  const [event] = xhsObservationEvents({
    observed_at: Date.UTC(2026, 6, 17),
    page_type: "explore",
    notes: [{
      note_id: "n".repeat(501),
      url: `https://www.xiaohongshu.com/explore/n?value=${"x".repeat(2_100)}`,
      title: "🙂".repeat(1_001),
    }],
  });

  assert.equal(event?.content_external_id, "n".repeat(500));
  assert.equal(event?.title, "🙂".repeat(1_000));
  assert.equal(event?.url, null);
});
