/**
 * Tests for the Douyin bootstrap RENDER_DATA reader.
 *
 * Task 4 of the Douyin bootstrap import plan
 * (docs/plans/2026-05-06-douyin-bootstrap-import.md).
 *
 * RENDER_DATA shape verified via chrome-devtools MCP probe 2026-05-07
 * against a real douyin.com tab — the script tag carries
 * URL-encoded JSON whose top key is `app`.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  decodeRenderData,
  extractDouyinSecUidFromRenderData,
  extractDouyinSecUidFromState,
  extractDouyinLoginState,
  reconcileDouyinSelfIdentity,
} from "../src/content/dy/bootstrap.ts";

test("decodeRenderData decodes URL-encoded JSON", () => {
  const obj = { app: { user: { secUid: "s1" } } };
  const encoded = encodeURIComponent(JSON.stringify(obj));
  assert.deepEqual(decodeRenderData(encoded), obj);
});

test("decodeRenderData returns null on malformed input", () => {
  assert.equal(decodeRenderData("%"), null); // bad URI escape
  assert.equal(decodeRenderData("not%20json"), null);
  assert.equal(decodeRenderData(""), null);
});

test("extractDouyinSecUidFromState finds sec_uid via canonical paths", () => {
  // Path 1: app.user.userInfo.secUid (camelCase variant)
  assert.equal(
    extractDouyinSecUidFromState({
      app: { user: { userInfo: { secUid: "abc" } } },
    }),
    "abc",
  );
  // Path 2: app.userStore.user.secUid (snake_case variant — we accept both)
  assert.equal(
    extractDouyinSecUidFromState({
      app: { userStore: { user: { sec_uid: "def" } } },
    }),
    "def",
  );
  // Path 3: app.odin.user_unique_id paths can vary; if no canonical
  // path matches, we return empty rather than guessing wrong.
  assert.equal(extractDouyinSecUidFromState({ app: {} }), "");
});

test("extractDouyinSecUidFromState handles malformed input", () => {
  assert.equal(extractDouyinSecUidFromState(null), "");
  assert.equal(extractDouyinSecUidFromState(undefined), "");
  assert.equal(extractDouyinSecUidFromState("string"), "");
  assert.equal(extractDouyinSecUidFromState({}), "");
});

test("extractDouyinSecUidFromRenderData decodes and resolves the account identity", () => {
  const raw = encodeURIComponent(
    JSON.stringify({
      app: { user: { userInfo: { sec_uid: "MS4wRenderUser", isLogin: true } } },
    }),
  );
  assert.equal(extractDouyinSecUidFromRenderData(raw), "MS4wRenderUser");
  assert.equal(extractDouyinSecUidFromRenderData("malformed"), "");
});

test("extractDouyinLoginState detects logged-in users", () => {
  // Logged-in requires an explicit positive flag; secUid is an identity
  // claim only and cannot establish the session state.
  assert.equal(
    extractDouyinLoginState({
      app: { user: { userInfo: { secUid: "abc", isLogin: true } } },
    }),
    true,
  );
  // Logged-out: explicit isLogin: false
  assert.equal(
    extractDouyinLoginState({
      app: { user: { userInfo: { isLogin: false } } },
    }),
    false,
  );
  // Missing fields default to logged-out — we never hallucinate login
  // state. Better to skip the bootstrap than corrupt the soul profile.
  assert.equal(extractDouyinLoginState({ app: {} }), false);
  assert.equal(extractDouyinLoginState(null), false);
});

test("RENDER_DATA rejects secUid when the page explicitly says logged out", () => {
  const state = {
    app: { user: { userInfo: { secUid: "MS4wGuestDevice", isLogin: false } } },
  };
  assert.equal(extractDouyinSecUidFromState(state), "MS4wGuestDevice");
  assert.equal(extractDouyinLoginState(state), false);
  assert.equal(
    extractDouyinSecUidFromRenderData(encodeURIComponent(JSON.stringify(state))),
    "",
  );

  const missingLoginFlag = {
    app: { user: { userInfo: { secUid: "MS4wUnverifiedClaim" } } },
  };
  assert.equal(extractDouyinLoginState(missingLoginFlag), false);
  assert.equal(
    extractDouyinSecUidFromRenderData(encodeURIComponent(JSON.stringify(missingLoginFlag))),
    "",
  );
});

test("profile/self is authoritative when RENDER_DATA identity conflicts", () => {
  assert.deepEqual(
    reconcileDouyinSelfIdentity({
      renderDataSecUid: "MS4wRenderUser",
      profileSelfSecUid: "MS4wProfileUser",
    }),
    {
      secUid: "MS4wProfileUser",
      source: "profile_self",
      conflict: true,
    },
  );
  assert.deepEqual(
    reconcileDouyinSelfIdentity({
      renderDataSecUid: "MS4wRenderUser",
      profileSelfSecUid: "",
      profileError: "identity_unavailable",
    }),
    {
      secUid: "",
      source: "",
      conflict: false,
      error: "identity_unavailable",
    },
  );
});
