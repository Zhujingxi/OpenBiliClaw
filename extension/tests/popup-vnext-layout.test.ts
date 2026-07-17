import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const html = readFileSync(new URL("../popup/popup.html", import.meta.url), "utf8");
const script = readFileSync(new URL("../popup/popup.js", import.meta.url), "utf8");
const style = html.match(/<style>([\s\S]*?)<\/style>/)?.[1] ?? "";

function assertIds(ids: string[]): void {
  for (const id of ids) {
    assert.match(html, new RegExp(`id="${id}"`), `missing retained #${id}`);
  }
}

function assertInOrder(markers: string[]): void {
  const positions = markers.map((marker) => html.indexOf(marker));
  assert.equal(positions.every((position) => position >= 0), true, `missing marker in ${markers.join(" -> ")}`);
  assert.deepEqual([...positions].sort((a, b) => a - b), positions, `wrong DOM order: ${markers.join(" -> ")}`);
}

test("popup keeps the original visual system and responsive side-panel contract", () => {
  assert.ok(
    style.split("\n").filter((line) => line.trim()).length > 2_500,
    "retained popup stylesheet was replaced with a reduced shell",
  );
  for (const selector of [
    ".side-panel-shell",
    ".hero-sub",
    ".webui-button",
    ".mobile-button",
    ".settings-gear",
    ".gh-star",
    ".recommendation-header-top",
    ".recommendation-summary-row",
    ".recommendation-status-row",
    ".recommendation-card",
    ".profile-edit-bar",
    ".profile-card",
    ".chat-shell",
    ".mobile-overlay",
    ".mobile-qr-card",
    ".settings-overlay",
    ".settings-tabs",
    ".settings-section",
    ".footer-head",
    ".footer-copy",
  ]) {
    assert.match(style, new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*\\{"), `missing original ${selector} styles`);
  }
  assert.match(style, /@media \(max-width: 360px\)/);
  assert.match(style, /@media \(max-width: 460px\)/);
  assert.match(style, /@media \(max-width: 520px\)/);
  assert.match(style, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(style, /@media \(max-width: 520px\), \(pointer: coarse\)/);
});

test("small vNext-only affordances inherit the retained visual tokens", () => {
  assert.match(style, /\.popup-toast\s*\{[\s\S]*?var\(--surface-strong\)/);
  assert.match(style, /\.mobile-qr-link\s*\{[\s\S]*?var\(--brand-soft\)/);
  assert.match(script, /target\.dataset\.tone = kind === "error" \? "error" : "success"/);
  assert.doesNotMatch(script, /target\.style\.background/);
});

test("popup preserves the original primary header and tab hierarchy", () => {
  assertIds([
    "statusBadge", "statusDot", "statusLabel", "openWebButton", "mobileQrButton",
    "settingsGear", "starButton", "starCount",
    "tabRecommend", "tabWatchLater", "tabFavorites", "tabProfile", "tabChat",
  ]);
  assert.match(html, /<header class="hero">[\s\S]*class="hero-top"[\s\S]*class="hero-sub"[\s\S]*<\/header>/);
  assert.match(html, /id="openWebButton" class="webui-button"[\s\S]*?<svg/);
  assert.match(html, /id="mobileQrButton" class="mobile-button"[\s\S]*?<svg/);
  assert.match(html, /id="settingsGear" class="settings-gear"[\s\S]*?<svg/);
  assert.match(html, /id="starButton" class="gh-star"/);
  assert.match(html, /class="tabs-shell"/);
  assert.match(html, /class="tab-bar"[^>]*role="tablist"/);
  assertInOrder(["tabRecommend", "tabWatchLater", "tabFavorites", "tabProfile", "tabChat"]);
  assertInOrder(["viewRecommend", "viewWatchLater", "viewFavorites", "viewProfile", "viewChat"].map((id) => `id="${id}"`));
  assert.match(html, /id="tabRecommend"[^>]*tabindex="0"/);
  for (const id of ["tabWatchLater", "tabFavorites", "tabProfile", "tabChat"]) {
    assert.match(html, new RegExp(`id="${id}"[^>]*tabindex="-1"`));
  }
});

test("popup retains broad original DOM regions for every retained journey", () => {
  assertIds([
    // Pairing and onboarding remain first-class journeys inside the original content shell.
    "mainContent", "pairingPanel", "pairingForm", "deviceKey", "endpointForm",
    "endpointScheme", "endpointHost", "endpointPort", "onboardingPanel", "onboardingForm",
    "onboardingSources", "onboardingBar", "onboardingStatus", "onboardingStart",
    // Feed.
    "viewRecommend", "refreshRecommendationsButton", "poolStatus", "poolAvailable",
    "poolReplenished", "poolTopics", "emptyState", "emptyTitle", "emptyText",
    "recommendationList",
    // Local library.
    "viewWatchLater", "watchLaterEmpty", "watchLaterList", "viewFavorites",
    "favoritesEmpty", "favoritesList",
    // Evidence profile.
    "viewProfile", "profileEditBar", "profileEditToggle", "profileEditHint", "profileEmpty",
    "profileEmptyTitle", "profileEmptyText", "profileCard", "profilePortrait", "profileFacetsView",
    "profileEditPanel", "profileForm", "profileNarrative", "profileFacets",
    // Chat.
    "viewChat", "chatMessages", "reloadChat", "chatForm", "chatInput", "chatLearn",
    "chatSendButton", "chatStatus",
  ]);
  assert.match(html, /<main id="mainContent" class="content">/);
  assert.match(html, /class="recommendation-header-card"[\s\S]*class="recommendation-header-top"/);
  assert.match(html, /id="poolStatus" class="recommendation-status-row"/);
  assert.match(html, /id="watchLaterEmpty" class="empty-state"/);
  assert.match(html, /id="favoritesEmpty" class="empty-state"/);
  assert.match(html, /id="profileCard" class="profile-card"/);
  assert.match(html, /id="profileEditBar" class="profile-edit-bar"/);
  assert.match(html, /<div class="chat-shell">[\s\S]*id="chatMessages" class="chat-messages"[\s\S]*id="chatForm" class="chat-form"/);
});

test("popup preserves original mobile, settings, and footer overlay structures", () => {
  assertIds([
    "mobileQrOverlay", "mobileQrBack", "mobileQrCode", "mobileQrUrl", "mobileQrHint",
    "mobileQrCopy", "mobileQrOpen", "settingsOverlay", "settingsBack", "settingsToast",
    "cfgBannerOffline", "cfgBannerDegraded", "settingsIssues", "settingsTabModels",
    "settingsTabSources", "settingsTabScheduler", "settingsTabGeneral", "settingsTabLogging",
    "settingsForm", "settingsPanelModels", "aliasHealth", "litellmAdmin", "settingsAiFields",
    "settingsPanelSources", "sourceList", "refreshSources", "settingsSourceFields",
    "settingsPanelScheduler", "settingsSchedulerFields", "settingsPanelGeneral",
    "settingsGeneralFields", "settingsPanelLogging", "settingsLoggingFields", "footerHintBar",
    "hintText", "headlineText", "reconnect", "toast",
  ]);
  assert.match(html, /id="mobileQrOverlay" class="mobile-overlay"[\s\S]*class="mobile-header"[\s\S]*class="mobile-qr-card"/);
  assert.match(html, /id="settingsOverlay" class="settings-overlay"[\s\S]*class="settings-header"[\s\S]*class="settings-tabs"/);
  assert.match(html, /id="settingsForm" class="settings-scroll"/);
  assert.match(html, /id="footerHintBar" class="footer"[\s\S]*class="footer-head"[\s\S]*class="footer-copy"/);
  assertInOrder(["mobileQrOverlay", "settingsOverlay", "footerHintBar", "toast"].map((id) => `id="${id}"`));
});

test("popup deletes only explicitly dropped product regions", () => {
  for (const dropped of [
    "delightSlot", "messagesButton", "messageBadge", "messagesOverlay", "messagesBack", "messagesList",
    "embeddingBanner", "embeddingBannerEnable", "embeddingBannerDismiss",
    "popupModelRouteTabs", "popupModelEditorBoundary", "popupModelRuntimeView", "popupModelMigrationPanel",
    "popupModelSaveButton", "backendUpdateCheck", "backendUpdateApply", "backendUpdateDownload",
    "watchLaterSyncAll", "watchLaterSyncStatus", "favoritesSyncAll", "favoritesSyncStatus",
    "cfgSavedAutoSync", "cfgAutoUpdate", "cfgSpeculationInterval", "profileMBTI",
    "profileSpeculativeInterests", "profileSpeculativeAvoidances", "profileActiveInsights",
    "profileRecentAwareness", "profileRecentMemory",
  ]) {
    assert.doesNotMatch(html, new RegExp(`id="${dropped}"`), `restored dropped #${dropped}`);
  }
});

test("popup pairing copy names the actual default backend port", () => {
  assert.match(html, /127\.0\.0\.1:8420/);
  assert.doesNotMatch(html, /127\.0\.0\.1:8765/);
});

test("retained popup navigation is wired and all API calls stay on generated vNext operations", () => {
  for (const marker of [
    "tabRecommend", "tabWatchLater", "tabFavorites", "tabProfile", "tabChat",
    "#settingsGear", "#settingsBack", "#mobileQrButton", "#mobileQrBack",
  ]) assert.match(script, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(script, /requestV1\("v1_/);
  assert.match(script, /readV1Sse\("v1_/);
  assert.match(script, /\$\("#starButton"\)\.addEventListener\("click"/);
  assert.match(script, /\$\("#statusDot"\)\.className/);
  assert.match(script, /\$\("#mobileQrCode"\)\.replaceChildren/);
  assert.match(script, /tab\.tabIndex = selected \? 0 : -1/);
  assert.match(script, /event\.key === "ArrowRight"/);
  assert.match(script, /event\.key === "ArrowLeft"/);
  assert.match(script, /nextTab\.focus\(\)/);
  assert.doesNotMatch(script, /["'`]\/api\/(?!v1(?:\/|["'`]))/);
});

test("vNext data renders through the original retained component contracts", () => {
  assert.match(script, /class:\s*"recommendation-card"/);
  assert.match(script, /class:\s*"recommendation-preview"/);
  assert.match(script, /class:\s*"recommendation-cover"/);
  assert.match(script, /class:\s*"recommendation-cover-text"/);
  assert.match(script, /class:\s*`recommendation-source-corner source-platform-\$\{sourceId\}`/);
  assert.match(script, /node\("img"/);
  assert.match(script, /class:\s*"recommendation-title"/);
  assert.match(script, /class:\s*"recommendation-meta-line"/);
  assert.match(script, /class:\s*"recommendation-actions"/);
  assert.match(script, /\$\("#emptyState"\)\.hidden/);
  assert.match(script, /\$\("#watchLaterEmpty"\)\.hidden/);
  assert.match(script, /\$\("#favoritesEmpty"\)\.hidden/);
  assert.match(script, /\$\("#profilePortrait"\)/);
  assert.match(script, /\$\("#profileFacetsView"\)/);
  assert.match(script, /class:\s*`chat-message/);
  assert.match(script, /class:\s*"settings-section settings-source-card"/);
  assert.match(script, /class:\s*"settings-section alias-card"/);
});

test("dropped feature CSS is not shipped after the retained stylesheet is restored", () => {
  for (const selector of [
    ".delight-banner", ".embedding-banner", ".messages-button", ".messages-overlay",
    ".popup-model-shell", ".saved-sync-toolbar", ".speculative-list", ".awareness-list",
    ".saved-toggle", ".saved-card", ".saved-load-retry", ".spec-specific",
  ]) {
    assert.doesNotMatch(style, new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
});
