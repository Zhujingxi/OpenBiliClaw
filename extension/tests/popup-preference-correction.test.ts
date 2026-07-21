import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const html = readFileSync(resolve("popup", "popup.html"), "utf8");
const js = readFileSync(resolve("popup", "popup.ts"), "utf8");

test("recommendation card has no preference correction entry", () => {
  const cardStart = html.indexOf('<div class="recommendation-header-card">');
  const cardEnd = html.indexOf('<div id="embeddingBanner"', cardStart);

  assert.notEqual(cardStart, -1, "recommendation card boundary should exist");
  assert.notEqual(cardEnd, -1, "embedding banner boundary should exist");
  assert.ok(cardEnd > cardStart, "recommendation card boundary should precede embedding banner");

  const card = html.slice(cardStart, cardEnd);

  assert.doesNotMatch(card, /推荐不准？/);
  assert.doesNotMatch(card, /编辑画像/);
  assert.doesNotMatch(card, /直接告诉阿B/);
  assert.doesNotMatch(card, /editProfileFromRecommendations/);
  assert.doesNotMatch(card, /chatFromRecommendations/);
  assert.doesNotMatch(html, /\.preference-correction-callout/);
});

test("popup bootstrap has no recommendation correction binding", () => {
  assert.doesNotMatch(js, /bindPreferenceCorrectionActions/);
  assert.doesNotMatch(js, /editProfileFromRecommendations/);
  assert.doesNotMatch(js, /chatFromRecommendations/);
});
