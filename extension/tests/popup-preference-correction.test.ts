import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const html = readFileSync(resolve("popup", "popup.html"), "utf8");
const js = readFileSync(resolve("popup", "popup.js"), "utf8");

test("recommendation header has no preference correction entry", () => {
  const header = html.slice(
    html.indexOf('class="recommendation-header-intro"'),
    html.indexOf('id="refreshRecommendationsButton"'),
  );

  assert.doesNotMatch(header, /推荐不准？/);
  assert.doesNotMatch(header, /编辑画像/);
  assert.doesNotMatch(header, /直接告诉阿B/);
  assert.doesNotMatch(header, /editProfileFromRecommendations/);
  assert.doesNotMatch(header, /chatFromRecommendations/);
  assert.doesNotMatch(html, /\.preference-correction-callout/);
});

test("popup bootstrap has no recommendation correction binding", () => {
  assert.doesNotMatch(js, /bindPreferenceCorrectionActions/);
  assert.doesNotMatch(js, /editProfileFromRecommendations/);
  assert.doesNotMatch(js, /chatFromRecommendations/);
});
