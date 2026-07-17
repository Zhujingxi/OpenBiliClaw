import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const html = readFileSync(new URL("../popup/popup.html", import.meta.url), "utf8");
const script = readFileSync(new URL("../popup/popup.js", import.meta.url), "utf8");

function positions(values: string[]): number[] {
  return values.map((value) => html.indexOf(value));
}

test("popup preserves the retained primary tab hierarchy and order", () => {
  const ids = [
    "tabRecommend",
    "tabWatchLater",
    "tabFavorites",
    "tabProfile",
    "tabChat",
  ];
  const panels = [
    "viewRecommend",
    "viewWatchLater",
    "viewFavorites",
    "viewProfile",
    "viewChat",
  ];

  const tabPositions = positions(ids.map((id) => `id="${id}"`));
  const panelPositions = positions(panels.map((id) => `id="${id}"`));
  assert.equal(tabPositions.every((position) => position >= 0), true);
  assert.deepEqual([...tabPositions].sort((a, b) => a - b), tabPositions);
  assert.equal(panelPositions.every((position) => position >= 0), true);
  assert.deepEqual([...panelPositions].sort((a, b) => a - b), panelPositions);
  assert.match(html, /class="tabs-shell"/);
  assert.match(html, /class="tab-bar"[^>]*role="tablist"/);
  assert.match(html, /class="content"/);
});

test("popup keeps retained header and settings overlay navigation", () => {
  for (const id of [
    "statusBadge",
    "openWebButton",
    "mobileQrButton",
    "settingsGear",
    "settingsOverlay",
    "settingsBack",
    "settingsTabModels",
    "settingsTabSources",
    "settingsTabScheduler",
    "settingsTabGeneral",
    "settingsTabLogging",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), `missing #${id}`);
  }
  assert.match(script, /\$\("#settingsGear"\)\.addEventListener\("click", showSettings\)/);
  assert.match(script, /\$\("#settingsBack"\)\.addEventListener\("click", hideSettings\)/);
  assert.match(script, /\$\("#settingsOverlay"\)\.hidden = false/);
  assert.match(script, /\$\("#settingsOverlay"\)\.hidden = true/);
  assert.match(script, /settingsTabModels|\[data-settings-tab\]/);
});

test("popup retains vNext journey surfaces without restoring dropped UI", () => {
  for (const id of [
    "pairingPanel",
    "onboardingPanel",
    "recommendationList",
    "watchLaterList",
    "favoritesList",
    "profileCard",
    "profileForm",
    "chatMessages",
    "chatForm",
    "sourceList",
    "aliasHealth",
    "litellmAdmin",
    "settingsForm",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), `missing #${id}`);
  }

  for (const dropped of [
    "delightSlot",
    "messagesButton",
    "messageBadge",
    "popupModelRouteTabs",
    "popupModelEditorBoundary",
    "backendUpdateCheck",
    "backendUpdateApply",
    "watchLaterSyncAll",
    "favoritesSyncAll",
    "cfgSavedAutoSync",
  ]) {
    assert.doesNotMatch(html, new RegExp(`id="${dropped}"`), `restored dropped #${dropped}`);
  }
});

test("popup pairing copy names the actual default backend port", () => {
  assert.match(html, /127\.0\.0\.1:8420/);
  assert.doesNotMatch(html, /127\.0\.0\.1:8765/);
});

test("retained popup navigation is wired and all API calls stay on generated vNext operations", () => {
  assert.match(script, /tabRecommend/);
  assert.match(script, /tabWatchLater/);
  assert.match(script, /tabFavorites/);
  assert.match(script, /tabProfile/);
  assert.match(script, /tabChat/);
  assert.match(script, /requestV1\("v1_/);
  assert.match(script, /readV1Sse\("v1_/);
  assert.doesNotMatch(script, /["'`]\/api\/(?!v1(?:\/|["'`]))/);
});
