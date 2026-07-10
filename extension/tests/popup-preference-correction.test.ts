import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const html = readFileSync(resolve("popup", "popup.html"), "utf8");
const js = readFileSync(resolve("popup", "popup.js"), "utf8");

test("recommendation header exposes preference correction controls", () => {
  assert.match(html, /推荐不准？/);
  assert.match(html, /id="editProfileFromRecommendations"/);
  assert.match(html, /id="chatFromRecommendations"/);
});

test("correction controls reuse profile edit and chat tabs", () => {
  assert.match(js, /setActiveTab\("profile"\)/);
  assert.match(js, /void enterProfileEditMode\(\)/);
  assert.match(js, /setActiveTab\("chat"\)/);
  assert.match(js, /requestAnimationFrame/);
  assert.match(js, /elements\.chatInput\.focus\(\)/);
});

test("correction controls wrap and preserve visible keyboard focus", () => {
  assert.match(html, /\.preference-correction-callout\s*\{[\s\S]*flex-wrap:\s*wrap/);
  assert.match(html, /\.preference-correction-callout button:focus-visible/);
  assert.match(html, /outline:/);
});
