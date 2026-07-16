/**
 * Tests for the Bilibili MAIN-world interact tap.
 *
 * The tap observes the user's own danmaku (`/x/v2/dm/post`) and comment
 * (`/x/v2/reply/add`) writes and posts them back to the isolated content
 * script (`content/bilibili.ts`), which builds a `comment` BEHAVIOR_EVENT.
 *
 * Fixture shapes are modelled on the community-documented bilibili write
 * APIs (bilibili-API-collect): form-encoded request bodies + a JSON response
 * whose top-level `code` is 0 on success. These are placeholders pending a
 * real end-to-end capture (see PR notes) — the parser matches endpoint paths
 * and the `code===0` business gate, both stable across the id/csrf details.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  classifyBiliInteractUrl,
  parseBiliInteract,
} from "../src/main/bili-interact-tap.ts";
import {
  buildEventFromBiliInteraction,
  isBiliInteraction,
} from "../src/content/bilibili.ts";

// ── classifyBiliInteractUrl ──────────────────────────────────────────────

test("classifyBiliInteractUrl maps the danmaku and comment write endpoints", () => {
  assert.equal(
    classifyBiliInteractUrl("https://api.bilibili.com/x/v2/dm/post"),
    "danmaku",
  );
  assert.equal(
    classifyBiliInteractUrl("https://api.bilibili.com/x/v2/reply/add"),
    "comment",
  );
  // Query strings are ignored.
  assert.equal(
    classifyBiliInteractUrl("https://api.bilibili.com/x/v2/dm/post?csrf=abc"),
    "danmaku",
  );
});

test("classifyBiliInteractUrl returns null for unrelated endpoints", () => {
  assert.equal(classifyBiliInteractUrl("https://api.bilibili.com/x/v2/reply/main"), null);
  assert.equal(classifyBiliInteractUrl("https://www.bilibili.com/video/BV1xx"), null);
  assert.equal(classifyBiliInteractUrl(""), null);
});

// ── parseBiliInteract ────────────────────────────────────────────────────

test("parseBiliInteract: successful dm/post → danmaku with text + bvid", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/v2/dm/post",
    requestBody:
      "type=1&oid=123456789&msg=" +
      encodeURIComponent("前方高能") +
      "&bvid=BV1xx411c7mD&progress=1000&csrf=abc",
    responseBody: JSON.stringify({ code: 0, message: "0", data: { dmid_str: "999" } }),
  });
  assert.equal(out?.kind, "danmaku");
  assert.equal(out?.text, "前方高能");
  assert.equal(out?.bvid, "BV1xx411c7mD");
  assert.equal(out?.oid, "123456789");
});

test("parseBiliInteract: successful reply/add → comment with message text", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/v2/reply/add",
    requestBody:
      "oid=123456789&type=1&message=" + encodeURIComponent("讲得真好") + "&plat=1&csrf=abc",
    responseBody: JSON.stringify({ code: 0, message: "0", data: { rpid_str: "42" } }),
  });
  assert.equal(out?.kind, "comment");
  assert.equal(out?.text, "讲得真好");
  assert.equal(out?.oid, "123456789");
});

test("parseBiliInteract: HTTP 2xx but code!==0 is dropped (business gate, invariant 7b)", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/v2/reply/add",
    requestBody: "oid=1&type=1&message=" + encodeURIComponent("被限流"),
    responseBody: JSON.stringify({ code: -412, message: "请求被拦截" }),
  });
  assert.equal(out, null);
});

test("parseBiliInteract: malformed response JSON → null (no throw)", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/v2/dm/post",
    requestBody: "oid=1&msg=hi&bvid=BV1",
    responseBody: "<html>gateway error</html>",
  });
  assert.equal(out, null);
});

test("parseBiliInteract: success but empty text field → null", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/v2/dm/post",
    requestBody: "oid=1&msg=&bvid=BV1",
    responseBody: JSON.stringify({ code: 0 }),
  });
  assert.equal(out, null);
});

test("parseBiliInteract: unrelated URL → null", () => {
  assert.equal(
    parseBiliInteract({
      url: "https://api.bilibili.com/x/web-interface/view",
      requestBody: "",
      responseBody: JSON.stringify({ code: 0 }),
    }),
    null,
  );
});

// ── content bridge: buildEventFromBiliInteraction ────────────────────────

test("buildEventFromBiliInteraction: danmaku → comment event, kind=danmaku, strength 0.6", () => {
  const event = buildEventFromBiliInteraction({
    kind: "danmaku",
    text: "太强了\n666", // \n (Cc) must be stripped by the sanitizer
    bvid: "BV1xx411c7mD",
  });
  assert.equal(event.type, "comment");
  assert.equal(event.source_platform, "bilibili");
  assert.equal(event.metadata.comment_kind, "danmaku");
  assert.equal(event.metadata.comment_text, "太强了666");
  assert.equal(event.metadata.signal_strength, 0.6);
  assert.equal(event.metadata.bvid, "BV1xx411c7mD");
});

test("buildEventFromBiliInteraction: comment → comment event, kind=comment, no forced strength", () => {
  const event = buildEventFromBiliInteraction({ kind: "comment", text: "讲得真好" });
  assert.equal(event.type, "comment");
  assert.equal(event.metadata.comment_kind, "comment");
  assert.equal(event.metadata.comment_text, "讲得真好");
  assert.equal(event.metadata.signal_strength, undefined);
});

test("isBiliInteraction validates kind + text", () => {
  assert.equal(isBiliInteraction({ kind: "danmaku", text: "hi" }), true);
  assert.equal(isBiliInteraction({ kind: "comment", text: "hi" }), true);
  assert.equal(isBiliInteraction({ kind: "bogus", text: "hi" }), false);
  assert.equal(isBiliInteraction({ kind: "danmaku" }), false);
  assert.equal(isBiliInteraction(null), false);
});
