/**
 * Tests for the Bangumi identity channel — the MAIN-world CHOBITS_UID
 * bridge parser and the isolated content script's nav-username extractor.
 *
 * Pure-helper tests on minimal fake documents so they run under
 * node --test without jsdom (same approach as xhs-passive.test.ts).
 */

import test from "node:test";
import assert from "node:assert/strict";

import { parseChobitsUid } from "../src/main/bgm-identity-bridge.ts";
import {
  extractBangumiUsernameFromDocument,
  uidFromBridgeMessage,
  usernameFromProfileHref,
} from "../src/content/bangumi.ts";

const BASE = "https://bgm.tv/";

test("parseChobitsUid accepts positive ints and rejects everything else", () => {
  assert.equal(parseChobitsUid(123456), 123456);
  assert.equal(parseChobitsUid("123456"), 123456);
  assert.equal(parseChobitsUid(" 42 "), 42);
  // Logged out / malformed shapes must all read as "no uid".
  assert.equal(parseChobitsUid(0), 0);
  assert.equal(parseChobitsUid("0"), 0);
  assert.equal(parseChobitsUid(-3), 0);
  assert.equal(parseChobitsUid(1.5), 0);
  assert.equal(parseChobitsUid(Number.NaN), 0);
  assert.equal(parseChobitsUid(undefined), 0);
  assert.equal(parseChobitsUid(null), 0);
  assert.equal(parseChobitsUid({}), 0);
  assert.equal(parseChobitsUid("abc"), 0);
});

test("usernameFromProfileHref extracts the /user/<name> segment", () => {
  assert.equal(usernameFromProfileHref("/user/sai", BASE), "sai");
  assert.equal(usernameFromProfileHref("/user/sai/collections", BASE), "sai");
  assert.equal(usernameFromProfileHref("https://bgm.tv/user/sai?x=1", BASE), "sai");
  assert.equal(usernameFromProfileHref("https://bangumi.tv/user/sai#top", BASE), "sai");
  // Non-user paths, foreign hosts, and junk yield "".
  assert.equal(usernameFromProfileHref("/subject/253", BASE), "");
  assert.equal(usernameFromProfileHref("https://evil.example/user/sai", BASE), "");
  assert.equal(usernameFromProfileHref("/user/", BASE), "");
  assert.equal(usernameFromProfileHref("", BASE), "");
  assert.equal(usernameFromProfileHref("/user/" + "x".repeat(200), BASE), "");
});

interface FakeAnchor {
  getAttribute(name: string): string | null;
}

function fakeDoc(selectorMap: Record<string, string[]>): Document {
  return {
    querySelectorAll(selector: string): FakeAnchor[] {
      const hrefs = selectorMap[selector] || [];
      return hrefs.map((href) => ({
        getAttribute: (name: string) => (name === "href" ? href : null),
      }));
    },
  } as unknown as Document;
}

test("extractBangumiUsernameFromDocument prefers the header idBadger anchor", () => {
  const doc = fakeDoc({
    "#headerNeue2 .idBadgerNeue a[href*='/user/']": ["/user/sai"],
    "#dock a[href*='/user/']": ["/user/other"],
  });
  assert.equal(extractBangumiUsernameFromDocument(doc, BASE), "sai");
});

test("extractBangumiUsernameFromDocument falls back to the dock anchor", () => {
  const dockDoc = fakeDoc({ "#dock a[href*='/user/']": ["/subject/1", "/user/dockuser"] });
  assert.equal(extractBangumiUsernameFromDocument(dockDoc, BASE), "dockuser");
});

test("extractBangumiUsernameFromDocument returns empty when logged out", () => {
  assert.equal(extractBangumiUsernameFromDocument(fakeDoc({}), BASE), "");
});

test("timeline stranger avatars never leak into the extracted username", () => {
  // Regression for the 2026-07-18 real-page E2E: on the anonymous bgm.tv
  // homepage the own-user selectors are all empty, but generic avatar
  // anchors match TIMELINE STRANGERS (/user/yuzzyu, /user/474349). Those
  // generic selectors were removed; a doc containing only stranger avatar
  // anchors must yield "" so the backend resolves identity from the uid.
  const anonymousHomepage = fakeDoc({
    "a.avatar[href*='/user/']": ["/user/yuzzyu", "/user/474349"],
    "#header a.avatar[href*='/user/']": ["/user/yuzzyu"],
  });
  assert.equal(extractBangumiUsernameFromDocument(anonymousHomepage, BASE), "");
  // Even alongside strangers, the own-only idBadger region still wins when
  // the user is actually logged in.
  const loggedInHomepage = fakeDoc({
    "a.avatar[href*='/user/']": ["/user/yuzzyu", "/user/474349"],
    ".idBadgerNeue a[href*='/user/']": ["/user/me-self"],
  });
  assert.equal(extractBangumiUsernameFromDocument(loggedInHomepage, BASE), "me-self");
});

test("uidFromBridgeMessage only accepts the bridge's own message shape", () => {
  assert.equal(uidFromBridgeMessage({ source: "obc-bgm-identity", uid: 42 }), 42);
  assert.equal(uidFromBridgeMessage({ source: "obc-bgm-identity", uid: 0 }), 0);
  assert.equal(uidFromBridgeMessage({ source: "obc-bgm-identity", uid: "42" }), 0);
  assert.equal(uidFromBridgeMessage({ source: "obc-xhs-state", uid: 42 }), 0);
  assert.equal(uidFromBridgeMessage(null), 0);
  assert.equal(uidFromBridgeMessage("obc-bgm-identity"), 0);
});

test("manifest registers the Bangumi hosts, content script, and MAIN bridge", async () => {
  const { readFileSync } = await import("node:fs");
  const { resolve } = await import("node:path");
  for (const file of ["manifest.json", "manifest.firefox.json"]) {
    const manifest = JSON.parse(readFileSync(resolve(file), "utf-8"));
    assert.ok(manifest.host_permissions.includes("*://*.bgm.tv/*"), `${file} bgm.tv host`);
    assert.ok(manifest.host_permissions.includes("*://*.bangumi.tv/*"), `${file} bangumi.tv host`);
    const scripts = manifest.content_scripts as Array<{
      matches: string[];
      js: string[];
      world?: string;
    }>;
    const bangumiScripts = scripts.filter((s) => s.matches.includes("*://*.bgm.tv/*"));
    assert.equal(bangumiScripts.length, 2, `${file} has isolated + MAIN entries`);
    assert.ok(
      bangumiScripts.some(
        (s) => s.world === "MAIN" && s.js.some((j) => j.includes("bgm-identity-bridge")),
      ),
      `${file} MAIN bridge`,
    );
    assert.ok(
      bangumiScripts.some((s) => !s.world && s.js.some((j) => j.includes("content/bangumi"))),
      `${file} isolated content script`,
    );
  }
});
