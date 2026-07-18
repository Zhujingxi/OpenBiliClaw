import assert from "node:assert/strict";
import test from "node:test";

import { handoffBehaviorEvent } from "../src/content/activity-handoff.ts";
import type { BehaviorEvent } from "../src/shared/types.ts";

const event: BehaviorEvent = {
  type: "view", url: "https://example.com", title: "x", timestamp: 1,
  source_platform: "reddit",
  context: { pageType: "post", viewport: { width: 1, height: 1 }, scrollPosition: 0 },
  metadata: { post_id: "p" },
};

test("content handoff retries the identical event after persistence rejection", async () => {
  const attempts: unknown[] = [];
  await handoffBehaviorEvent(event, async (message) => {
    attempts.push(message);
    return { accepted: attempts.length > 1 };
  }, async () => undefined);
  assert.equal(attempts.length, 2);
  assert.deepEqual(attempts[0], attempts[1]);
});
