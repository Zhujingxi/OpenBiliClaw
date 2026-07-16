/**
 * Tests for the Bilibili MAIN-world interact tap.
 *
 * The tap observes the user's own danmaku/comment and video like/favorite/
 * coin writes and posts them back to the isolated content script
 * (`content/bilibili.ts`), which builds the corresponding BEHAVIOR_EVENT.
 *
 * PENDING REAL-DEVICE VALIDATION: fixture shapes are modelled on the
 * community-documented bilibili write APIs (bilibili-API-collect):
 * form-encoded request bodies + a JSON response whose top-level `code` is 0
 * on success. The parser matches endpoint paths, documented action fields,
 * HTTP 2xx, and the `code===0` business gate.
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

test("classifyBiliInteractUrl maps the captured Bilibili write endpoints", () => {
  assert.equal(
    classifyBiliInteractUrl("https://api.bilibili.com/x/v2/dm/post"),
    "danmaku",
  );
  assert.equal(
    classifyBiliInteractUrl("https://api.bilibili.com/x/v2/reply/add"),
    "comment",
  );
  assert.equal(
    classifyBiliInteractUrl("https://api.bilibili.com/x/web-interface/archive/like"),
    "archive-like",
  );
  assert.equal(
    classifyBiliInteractUrl("https://api.bilibili.com/x/v3/fav/resource/deal"),
    "favorite-deal",
  );
  assert.equal(
    classifyBiliInteractUrl("https://api.bilibili.com/x/web-interface/coin/add"),
    "coin",
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
    responseStatus: 200,
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
    responseStatus: 200,
    requestBody:
      "oid=123456789&type=1&message=" + encodeURIComponent("讲得真好") + "&plat=1&csrf=abc",
    responseBody: JSON.stringify({ code: 0, message: "0", data: { rpid_str: "42" } }),
  });
  assert.equal(out?.kind, "comment");
  assert.equal(out?.text, "讲得真好");
  assert.equal(out?.oid, "123456789");
});

test("parseBiliInteract: archive like=1 → like", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/web-interface/archive/like",
    requestBody: "bvid=BV1xx411c7mD&like=1&csrf=abc",
    responseStatus: 200,
    responseBody: JSON.stringify({ code: 0, message: "0" }),
  });
  assert.deepEqual(out, { kind: "like", bvid: "BV1xx411c7mD" });
});

test("parseBiliInteract: archive like=2 → retraction of like", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/web-interface/archive/like",
    requestBody: "aid=123456789&like=2&csrf=abc",
    responseStatus: 200,
    responseBody: JSON.stringify({ code: 0, message: "0" }),
  });
  assert.deepEqual(out, { kind: "retraction", retracted_action: "like" });
});

test("parseBiliInteract: non-empty add_media_ids → favorite", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/v3/fav/resource/deal",
    requestBody: "rid=123456789&type=2&add_media_ids=987654&del_media_ids=&csrf=abc",
    responseStatus: 200,
    responseBody: JSON.stringify({ code: 0, message: "0" }),
  });
  assert.deepEqual(out, { kind: "favorite" });
});

test("parseBiliInteract: non-empty del_media_ids → retraction of favorite", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/v3/fav/resource/deal",
    requestBody: "rid=123456789&type=2&add_media_ids=&del_media_ids=987654&csrf=abc",
    responseStatus: 200,
    responseBody: JSON.stringify({ code: 0, message: "0" }),
  });
  assert.deepEqual(out, { kind: "retraction", retracted_action: "favorite" });
});

test("parseBiliInteract: successful coin/add → coin", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/web-interface/coin/add",
    requestBody: "bvid=BV1xx411c7mD&multiply=1&select_like=0&csrf=abc",
    responseStatus: 200,
    responseBody: JSON.stringify({ code: 0, message: "0" }),
  });
  assert.deepEqual(out, { kind: "coin", bvid: "BV1xx411c7mD" });
});

test("parseBiliInteract: HTTP non-2xx is dropped even when code===0", () => {
  assert.equal(
    parseBiliInteract({
      url: "https://api.bilibili.com/x/web-interface/archive/like",
      requestBody: "bvid=BV1xx411c7mD&like=1",
      responseStatus: 503,
      responseBody: JSON.stringify({ code: 0 }),
    }),
    null,
  );
});

test("parseBiliInteract: successful response with bad action payload is dropped", () => {
  const responseBody = JSON.stringify({ code: 0 });
  assert.equal(
    parseBiliInteract({
      url: "https://api.bilibili.com/x/web-interface/archive/like",
      requestBody: "aid=1&like=unexpected",
      responseStatus: 200,
      responseBody,
    }),
    null,
  );
  assert.equal(
    parseBiliInteract({
      url: "https://api.bilibili.com/x/v3/fav/resource/deal",
      requestBody: "rid=1&type=2&add_media_ids=&del_media_ids=",
      responseStatus: 200,
      responseBody,
    }),
    null,
  );
});

test("parseBiliInteract: HTTP 2xx but code!==0 is dropped (business gate, invariant 7b)", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/web-interface/archive/like",
    requestBody: "bvid=BV1xx411c7mD&like=1",
    responseStatus: 200,
    responseBody: JSON.stringify({ code: -412, message: "请求被拦截" }),
  });
  assert.equal(out, null);
});

test("parseBiliInteract: malformed response JSON → null (no throw)", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/v2/dm/post",
    requestBody: "oid=1&msg=hi&bvid=BV1",
    responseStatus: 200,
    responseBody: "<html>gateway error</html>",
  });
  assert.equal(out, null);
});

test("parseBiliInteract: success but empty text field → null", () => {
  const out = parseBiliInteract({
    url: "https://api.bilibili.com/x/v2/dm/post",
    requestBody: "oid=1&msg=&bvid=BV1",
    responseStatus: 200,
    responseBody: JSON.stringify({ code: 0 }),
  });
  assert.equal(out, null);
});

test("parseBiliInteract: unrelated URL → null", () => {
  assert.equal(
    parseBiliInteract({
      url: "https://api.bilibili.com/x/web-interface/view",
      requestBody: "",
      responseStatus: 200,
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

test("buildEventFromBiliInteraction: like/favorite/coin map to their positive event types", () => {
  for (const kind of ["like", "favorite", "coin"] as const) {
    const event = buildEventFromBiliInteraction({ kind });
    assert.equal(event.type, kind);
    assert.equal(event.source_platform, "bilibili");
  }
});

test("buildEventFromBiliInteraction: retraction aligns with the kernel payload", () => {
  const event = buildEventFromBiliInteraction({
    kind: "retraction",
    retracted_action: "favorite",
  });
  assert.equal(event.type, "feedback");
  assert.equal(event.metadata.feedback_type, "retraction");
  assert.equal(event.metadata.retracted_action, "favorite");
  assert.equal(event.metadata.signal_strength, 0.2);
});

test("isBiliInteraction validates comment text and action/retraction shapes", () => {
  assert.equal(isBiliInteraction({ kind: "danmaku", text: "hi" }), true);
  assert.equal(isBiliInteraction({ kind: "comment", text: "hi" }), true);
  assert.equal(isBiliInteraction({ kind: "like" }), true);
  assert.equal(isBiliInteraction({ kind: "favorite" }), true);
  assert.equal(isBiliInteraction({ kind: "coin" }), true);
  assert.equal(
    isBiliInteraction({ kind: "retraction", retracted_action: "like" }),
    true,
  );
  assert.equal(isBiliInteraction({ kind: "retraction" }), false);
  assert.equal(
    isBiliInteraction({ kind: "retraction", retracted_action: "coin" }),
    false,
  );
  assert.equal(isBiliInteraction({ kind: "bogus", text: "hi" }), false);
  assert.equal(isBiliInteraction({ kind: "danmaku" }), false);
  assert.equal(isBiliInteraction(null), false);
});
