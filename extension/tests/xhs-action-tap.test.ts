/**
 * Tests for the Xiaohongshu MAIN-world action tap + its content-script bridge.
 *
 * The tap observes the user's own like (`/note/like`), un-like
 * (`/note/dislike`), collect (`/note/collect`) and un-collect
 * (`/note/uncollect`) writes and posts them back to the isolated content
 * script (`content/xiaohongshu.ts` → `xhs/action-event.ts`), which builds a
 * like / favorite / retraction BEHAVIOR_EVENT.
 *
 * Fixture shapes are modelled on the public community-documented xhs web APIs
 * (host `edith.xiaohongshu.com`; `{"success":true,"code":0,...}` response) and
 * are placeholders pending a real end-to-end capture (see PR notes). The parser
 * matches endpoint paths, a 24-hex note id in the request body, and the
 * `success===true` / `code===0` business gate — all stable across csrf/session
 * details.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  classifyXhsActionUrl,
  parseXhsAction,
} from "../src/main/xhs-action-tap.ts";
import {
  buildEventFromXhsAction,
  isXhsAction,
  xhsNoteUrl,
} from "../src/content/xhs/action-event.ts";

const NOTE_ID = "69dea966000000001a0280ad";
const EDITH = "https://edith.xiaohongshu.com/api/sns/web/v1/note";
const OK = JSON.stringify({ success: true, code: 0, msg: "成功" });

// ── classifyXhsActionUrl ─────────────────────────────────────────────────

test("classifyXhsActionUrl maps the four like/collect write endpoints", () => {
  assert.equal(classifyXhsActionUrl(`${EDITH}/like`)?.type, "like");
  assert.equal(classifyXhsActionUrl(`${EDITH}/collect`)?.type, "favorite");
  const dislike = classifyXhsActionUrl(`${EDITH}/dislike`);
  assert.equal(dislike?.type, "retraction");
  assert.equal(dislike?.retracted, "like");
  const uncollect = classifyXhsActionUrl(`${EDITH}/uncollect`);
  assert.equal(uncollect?.type, "retraction");
  assert.equal(uncollect?.retracted, "favorite");
  // Query strings are ignored.
  assert.equal(classifyXhsActionUrl(`${EDITH}/like?xsec=abc`)?.type, "like");
});

test("classifyXhsActionUrl returns null for unrelated / token-sniffer endpoints", () => {
  assert.equal(classifyXhsActionUrl(`${EDITH}/feed`), null);
  assert.equal(
    classifyXhsActionUrl("https://edith.xiaohongshu.com/api/sns/web/v1/search/notes"),
    null,
  );
  assert.equal(classifyXhsActionUrl(""), null);
});

// ── parseXhsAction ───────────────────────────────────────────────────────

test("parseXhsAction: successful like → like with note_id", () => {
  const out = parseXhsAction({
    url: `${EDITH}/like`,
    requestBody: JSON.stringify({ note_oid: NOTE_ID }),
    responseBody: OK,
  });
  assert.deepEqual(out, { type: "like", note_id: NOTE_ID });
});

test("parseXhsAction: successful collect → favorite with note_id", () => {
  const out = parseXhsAction({
    url: `${EDITH}/collect`,
    requestBody: JSON.stringify({ note_id: NOTE_ID }),
    responseBody: OK,
  });
  assert.deepEqual(out, { type: "favorite", note_id: NOTE_ID });
});

test("parseXhsAction: dislike → retraction of a like", () => {
  const out = parseXhsAction({
    url: `${EDITH}/dislike`,
    requestBody: JSON.stringify({ note_id: NOTE_ID }),
    responseBody: OK,
  });
  assert.deepEqual(out, { type: "retraction", note_id: NOTE_ID, retracted_action: "like" });
});

test("parseXhsAction: uncollect → retraction of a favorite", () => {
  const out = parseXhsAction({
    url: `${EDITH}/uncollect`,
    requestBody: JSON.stringify({ note_id: NOTE_ID }),
    responseBody: OK,
  });
  assert.deepEqual(out, {
    type: "retraction",
    note_id: NOTE_ID,
    retracted_action: "favorite",
  });
});

test("parseXhsAction: HTTP 2xx but business failure is dropped (invariant 7b)", () => {
  // success:false / non-zero code → not a real action.
  assert.equal(
    parseXhsAction({
      url: `${EDITH}/like`,
      requestBody: JSON.stringify({ note_id: NOTE_ID }),
      responseBody: JSON.stringify({ success: false, code: -100, msg: "登录失效" }),
    }),
    null,
  );
});

test("parseXhsAction: malformed response JSON → null (no throw)", () => {
  assert.equal(
    parseXhsAction({
      url: `${EDITH}/like`,
      requestBody: JSON.stringify({ note_id: NOTE_ID }),
      responseBody: "<html>403</html>",
    }),
    null,
  );
});

test("parseXhsAction: success but no recoverable note id → null", () => {
  assert.equal(
    parseXhsAction({
      url: `${EDITH}/like`,
      requestBody: JSON.stringify({ something: "else" }),
      responseBody: OK,
    }),
    null,
  );
});

test("parseXhsAction: unknown endpoint → null even on success", () => {
  assert.equal(
    parseXhsAction({
      url: `${EDITH}/share`,
      requestBody: JSON.stringify({ note_id: NOTE_ID }),
      responseBody: OK,
    }),
    null,
  );
});

test("parseXhsAction: note id recovered from response when request body lacks it", () => {
  const out = parseXhsAction({
    url: `${EDITH}/like`,
    requestBody: "",
    responseBody: JSON.stringify({ success: true, code: 0, data: { note_id: NOTE_ID } }),
  });
  assert.deepEqual(out, { type: "like", note_id: NOTE_ID });
});

// ── content bridge: buildEventFromXhsAction ──────────────────────────────

test("buildEventFromXhsAction: like → like event with note_id + canonical url", () => {
  const event = buildEventFromXhsAction({ type: "like", note_id: NOTE_ID });
  assert.equal(event.type, "like");
  assert.equal(event.source_platform, "xiaohongshu");
  assert.equal(event.url, `https://www.xiaohongshu.com/explore/${NOTE_ID}`);
  assert.equal(event.metadata.note_id, NOTE_ID);
  assert.equal(event.metadata.feedback_type, undefined);
});

test("buildEventFromXhsAction: favorite → favorite event", () => {
  const event = buildEventFromXhsAction({ type: "favorite", note_id: NOTE_ID });
  assert.equal(event.type, "favorite");
  assert.equal(event.metadata.note_id, NOTE_ID);
});

test("buildEventFromXhsAction: retraction → feedback event, strength 0.2", () => {
  const event = buildEventFromXhsAction({
    type: "retraction",
    note_id: NOTE_ID,
    retracted_action: "like",
  });
  assert.equal(event.type, "feedback");
  assert.equal(event.metadata.feedback_type, "retraction");
  assert.equal(event.metadata.retracted_action, "like");
  assert.equal(event.metadata.signal_strength, 0.2);
});

// ── isolation from the token sniffer + shape validation ──────────────────

test("isXhsAction validates type + 24-hex note_id, rejects token-sniffer pairs", () => {
  assert.equal(isXhsAction({ type: "like", note_id: NOTE_ID }), true);
  assert.equal(isXhsAction({ type: "retraction", note_id: NOTE_ID }), true);
  // A `{note_id, xsec_token}` pair (the sniffer's payload shape) has no valid
  // action type → never mistaken for an action.
  assert.equal(isXhsAction({ note_id: NOTE_ID, xsec_token: "abc" }), false);
  assert.equal(isXhsAction({ type: "like", note_id: "short" }), false);
  assert.equal(isXhsAction({ type: "bogus", note_id: NOTE_ID }), false);
  assert.equal(isXhsAction(null), false);
});

test("xhsNoteUrl round-trips into the note-detail URL shape the backend keys on", () => {
  // Mirrors sources/identity_keys.py note_id_from_url: /explore/<24hex>.
  const url = xhsNoteUrl(NOTE_ID);
  assert.match(url, /xiaohongshu\.com\/explore\/[0-9a-f]{24}$/i);
});
