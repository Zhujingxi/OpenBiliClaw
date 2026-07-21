import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

import {
  formatPublishedTime,
  normalizeDelightCandidate,
  normalizeRecommendation,
} from "../popup/popup-helpers.js";

const popupJs = readFileSync(resolve("popup", "popup.ts"), "utf8");
const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");

function functionSource(name: string): string {
  const start = popupJs.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} function not found`);
  const openingBrace = popupJs.indexOf("{", start);
  let depth = 0;
  for (let index = openingBrace; index < popupJs.length; index += 1) {
    if (popupJs[index] === "{") depth += 1;
    if (popupJs[index] === "}") depth -= 1;
    if (depth === 0) return popupJs.slice(start, index + 1);
  }
  throw new Error(`${name} closing brace not found`);
}

test("popup publication time prefers exact time and falls back to label", () => {
  const now = new Date(2026, 6, 11, 12, 0, 0, 0).getTime();
  const iso = (offset: number) => new Date(now + offset).toISOString();
  const exact = normalizeRecommendation({
    id: 1,
    bvid: "BV1",
    published_at: iso(-10_800_000),
    published_label: "fallback",
  });
  const delight = normalizeDelightCandidate({
    bvid: "BV2",
    published_label: "  3   days ago\n",
  });

  assert.equal(exact.published_at, iso(-10_800_000));
  assert.equal(exact.published_label, "fallback");
  assert.equal(formatPublishedTime(exact, now), "3 小时前");
  assert.equal(formatPublishedTime(delight, now), "3 days ago");
  assert.equal(formatPublishedTime({}, now), "");
  assert.equal(
    formatPublishedTime({ published_at: "not-a-date", published_label: "来源时间" }, now),
    "来源时间",
  );
});

test("popup publication formatter matches shared exact-time boundaries", () => {
  const now = new Date(2026, 6, 11, 12, 0, 0, 0).getTime();
  const iso = (offset: number) => new Date(now + offset).toISOString();
  const cases: Array<[object, string]> = [
    [{ published_at: iso(-59_999) }, "刚刚"],
    [{ published_at: iso(-60_000) }, "1 小时前"],
    [{ published_at: iso(-86_399_999) }, "23 小时前"],
    [{ published_at: iso(-86_400_000) }, "1 天前"],
    [{ published_at: iso(-604_799_999) }, "6 天前"],
    [{ published_at: iso(-604_800_000) }, "7月4日"],
    [{ published_at: new Date(2026, 0, 2, 12).toISOString() }, "1月2日"],
    [{ published_at: new Date(2025, 10, 9, 12).toISOString() }, "2025-11-09"],
    [{ published_at: iso(300_000) }, "刚刚"],
    [{ published_at: iso(300_001) }, "7月11日"],
  ];

  for (const [item, expected] of cases) {
    assert.equal(formatPublishedTime(item, now), expected);
  }
});

test("recommendation and delight renderers append optional publication metadata", () => {
  const renderRecommendations = popupJs.slice(
    popupJs.indexOf("function renderRecommendations"),
    popupJs.indexOf("function renderRecommendations") + 5000,
  );
  const buildDelightCard = popupJs.slice(
    popupJs.indexOf("function buildDelightCard"),
    popupJs.indexOf("function buildDelightCard") + 4000,
  );
  const appendPublishedTime = popupJs.slice(
    popupJs.indexOf("function appendPublishedTime"),
    popupJs.indexOf("function appendPublishedTime") + 700,
  );

  assert.match(renderRecommendations, /appendPublishedTime\(metaLine, item\)/);
  assert.match(buildDelightCard, /appendPublishedTime\(textCol, delight\)/);
  assert.match(appendPublishedTime, /if \(!text\) return;/);
  assert.match(appendPublishedTime, /textContent = text/);
  assert.match(appendPublishedTime, /className = "recommendation-published-time"/);
});

test("visible pending delight banner appends publication time to its metadata line", () => {
  const renderDelightSlot = functionSource("renderDelightSlot");
  const renderMessagesList = functionSource("renderMessagesList");

  assert.match(renderMessagesList, /if \(type === "delight"\) continue/);
  assert.match(renderDelightSlot, /const kickerLine = document\.createElement\("span"\)/);
  assert.match(
    renderDelightSlot,
    /kickerLine\.append\(platformChip\);\s*appendPublishedTime\(kickerLine, delight\);/,
  );
  assert.match(renderDelightSlot, /textCol\.append\(kickerLine, titleText\)/);
  assert.match(renderDelightSlot, /elements\.delightSlot\.replaceChildren\(banner\)/);
});

test("popup publication metadata uses the muted token", () => {
  const block = popupHtml.match(/\.recommendation-published-time\s*\{[\s\S]*?\}/);
  assert.ok(block, "missing .recommendation-published-time CSS rule");
  assert.ok(block[0].includes("var(--text-muted)"));
});
