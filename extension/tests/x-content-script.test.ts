/**
 * Tests for the X (Twitter) content-script entry's pure helpers.
 *
 * The entry wires the collector kernel + the MAIN-world tap bridge, both
 * of which need a DOM. Those side effects are guarded behind a
 * `typeof window` check so this module imports cleanly under node --test,
 * exposing the two pure helpers that carry the normalization logic:
 *
 *   - isXEngagement(value) — validates a postMessage payload, now
 *     including the "retraction" type.
 *   - buildEventFromEngagement(engagement) — maps an XEngagement to the
 *     unified BehaviorEvent the service worker forwards to /api/events.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { buildEventFromEngagement, isXEngagement } from "../src/content/x.ts";
import type { XEngagement } from "../src/main/x-graphql-tap.ts";

test("isXEngagement accepts a retraction payload carrying a tweet_id", () => {
  assert.equal(
    isXEngagement({ type: "retraction", tweet_id: "1", retracted_action: "like" }),
    true,
  );
});

test("isXEngagement still accepts the existing engagement types", () => {
  assert.equal(isXEngagement({ type: "like", tweet_id: "1" }), true);
  assert.equal(isXEngagement({ type: "follow", user_id: "44196397" }), true);
});

test("isXEngagement rejects unknown types and id-less payloads", () => {
  assert.equal(isXEngagement({ type: "bogus", tweet_id: "1" }), false);
  assert.equal(isXEngagement({ type: "like" }), false);
  assert.equal(isXEngagement(null), false);
});

test("buildEventFromEngagement normalizes a retraction into a feedback event", () => {
  const engagement: XEngagement = {
    type: "retraction",
    tweet_id: "1790000000000000010",
    retracted_action: "like",
  };
  const event = buildEventFromEngagement(engagement);
  assert.equal(event.type, "feedback");
  assert.equal(event.metadata.feedback_type, "retraction");
  assert.equal(event.metadata.retracted_action, "like");
  assert.equal(event.metadata.signal_strength, 0.2);
  assert.equal(event.metadata.tweet_id, "1790000000000000010");
  assert.equal(event.source_platform, "twitter");
});

test("buildEventFromEngagement keeps positive engagements unchanged", () => {
  const event = buildEventFromEngagement({ type: "like", tweet_id: "1" });
  assert.equal(event.type, "like");
  assert.equal(event.metadata.feedback_type, undefined);
  assert.equal(event.metadata.tweet_id, "1");
});

test("buildEventFromEngagement writes sanitized comment_text + comment_kind for a reply", () => {
  const event = buildEventFromEngagement({
    type: "comment",
    tweet_id: "1790000000000000004",
    text: "great\nthread \u200b thanks", // \n (Cc) + zero-width space (Cf) strip
  });
  assert.equal(event.type, "comment");
  assert.equal(event.metadata.comment_kind, "comment");
  assert.equal(event.metadata.comment_text, "greatthread  thanks");
  assert.ok(!String(event.metadata.comment_text).includes("\n"));
});

test("buildEventFromEngagement omits comment_text when a reply carries no body", () => {
  const event = buildEventFromEngagement({ type: "comment", tweet_id: "5" });
  assert.equal(event.type, "comment");
  assert.equal(event.metadata.comment_text, undefined);
  assert.equal(event.metadata.comment_kind, undefined);
});
