import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("profile cognition auto-load listens to the shared content scroller", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /content:\s*document\.querySelector\("\.content"\)/);
  assert.match(popupJs, /elements\.content\.scrollHeight - elements\.content\.scrollTop - elements\.content\.clientHeight/);
  assert.match(popupJs, /elements\.content\.addEventListener\("scroll"/);
  assert.match(popupJs, /maybeLoadMoreRecommendations\(\)/);
  assert.doesNotMatch(popupJs, /elements\.viewProfile\.addEventListener\("scroll"/);
});

test("recommendation covers do not rely on native lazy loading inside the popup scroller", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(popupJs, /const image = document\.createElement\("img"\);/);
  assert.doesNotMatch(popupJs, /image\.loading = "lazy"/);
});
