import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import { sendBehaviorEvents } from "../popup/popup-api.js";
import { __resetBackendEndpointForTests } from "../popup/popup-backend-config.js";
import { __resetPopupDeviceAuthForTests } from "../popup/popup-device-auth.js";

const popupSource = readFileSync(resolve("popup", "popup.js"), "utf8");
const popupApiSource = readFileSync(resolve("popup", "popup-api.js"), "utf8");
const popupHtmlSource = readFileSync(resolve("popup", "popup.html"), "utf8");

function sourceBetween(source: string, startMarker: string, endMarker: string) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(start, -1, `missing source marker: ${startMarker}`);
  assert.notEqual(end, -1, `missing source marker: ${endMarker}`);
  return source.slice(start, end);
}

test("popup behavior events post saved feedback to /events", async () => {
  __resetBackendEndpointForTests();
  __resetPopupDeviceAuthForTests();
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = (async (url: string, options: RequestInit = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { accepted: 1, rejected: [] };
      },
    };
  }) as unknown as typeof fetch;

  const events = [{
    type: "feedback",
    source_platform: "bilibili",
    title: "saved item",
    url: "https://www.bilibili.com/video/BV1SAVED",
    timestamp: 123,
    metadata: {
      feedback_type: "like",
      bvid: "BV1SAVED",
      content_id: "BV1SAVED",
      feedback_note: "",
      saved_feedback: true,
    },
  }];

  try {
    const result = await sendBehaviorEvents(events);

    assert.deepEqual(result, { accepted: 1, rejected: [] });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "http://127.0.0.1:8420/api/events");
    assert.equal(calls[0].options.method, "POST");
    assert.equal(typeof events[0].event_id, "string");
    assert.ok(events[0].event_id.length > 0);
    assert.deepEqual(JSON.parse(String(calls[0].options.body)), { events });
    assert.doesNotMatch(calls[0].url, /\/feedback$/);
  } finally {
    globalThis.fetch = originalFetch;
    __resetPopupDeviceAuthForTests();
    __resetBackendEndpointForTests();
  }
});

test("saved cards render icon feedback and exactly one conditional cross-list toggle", () => {
  const savedCardSource = sourceBetween(
    popupSource,
    "function buildSavedCard(",
    "\nfunction buildSavedCardMedia(",
  );

  assert.match(savedCardSource, /className = "saved-card-feedback"/);
  for (const action of ["like", "dislike", "comment"]) {
    assert.match(savedCardSource, new RegExp(`dataset\\.savedAction = "${action}"`));
  }
  assert.equal(savedCardSource.match(/className = "feedback-icon-btn"/g)?.length, 3);
  assert.match(savedCardSource, /like\.innerHTML = THUMBS_UP_ICON_SVG/);
  assert.match(savedCardSource, /dislike\.innerHTML = THUMBS_DOWN_ICON_SVG/);
  assert.match(savedCardSource, /comment\.innerHTML = MESSAGE_ICON_SVG/);
  assert.match(savedCardSource, /const crossIsFavorite = listKind === "watch_later"/);
  assert.equal(savedCardSource.match(/const crossToggle = createActionButton/g)?.length, 1);
  assert.match(
    savedCardSource,
    /crossToggle\.dataset\.savedAction = crossIsFavorite \? "favorite" : "watch-later"/,
  );
  assert.match(savedCardSource, /feedbackActions\.append\(like, dislike, comment, crossToggle\)/);
  assert.match(savedCardSource, /bindFavoriteToggle\(crossToggle, item\)/);
  assert.match(savedCardSource, /bindWatchLaterToggle\(crossToggle, item\)/);
  assert.doesNotMatch(savedCardSource, /className = "probe-btn/);
  assert.doesNotMatch(savedCardSource, /\.textContent = "喜欢"|\.textContent = "不感兴趣"|\.textContent = "聊一聊"/);
  assert.doesNotMatch(savedCardSource, /dismiss/i);
});

test("saved feedback row uses ghost icon CSS with a right-aligned cross toggle", () => {
  const feedbackCss = sourceBetween(
    popupHtmlSource,
    "    .saved-card-feedback {",
    "    .saved-card-sync,",
  );

  assert.match(feedbackCss, /\.saved-card-feedback button \{/);
  assert.match(feedbackCss, /width: 36px/);
  assert.match(feedbackCss, /height: 36px/);
  assert.match(feedbackCss, /border: 0/);
  assert.match(feedbackCss, /background: transparent/);
  assert.match(feedbackCss, /color: var\(--text-muted/);
  assert.match(feedbackCss, /button:hover \{ background: var\(--brand-soft/);
  assert.match(feedbackCss, /button:active [^{]*\{[^}]*transform: scale\(0\.94\)/);
  assert.match(feedbackCss, /\.cross-toggle \{ margin-left: auto/);
  assert.match(feedbackCss, /favorite-btn\[aria-pressed="true"\][^\{]*\{[^}]*color: #e8a33d/);
  assert.doesNotMatch(feedbackCss, /\.saved-card-feedback \.probe-btn/);
});

test("saved-card like dislike and comment use content events, never recommendation feedback", () => {
  const postSavedFeedbackSource = sourceBetween(
    popupSource,
    "async function postSavedFeedback(",
    "\nasync function handleSavedCardFeedback(",
  );
  const feedbackHandlerSource = sourceBetween(
    popupSource,
    "async function handleSavedCardFeedback(",
    "\nfunction buildSavedCard(",
  );
  const savedCardSource = sourceBetween(
    popupSource,
    "function buildSavedCard(",
    "\nfunction buildSavedCardMedia(",
  );
  const behaviorApiSource = sourceBetween(
    popupApiSource,
    "export async function sendBehaviorEvents(",
    "\n/**\n * Report a click-through",
  );

  assert.match(postSavedFeedbackSource, /sendBehaviorEvents\(\[\{/);
  assert.match(postSavedFeedbackSource, /type: "feedback"/);
  assert.match(postSavedFeedbackSource, /url: buildContentUrl\(item\) \|\| item\.content_url \|\| ""/);
  assert.match(postSavedFeedbackSource, /feedback_type: feedbackType/);
  assert.match(postSavedFeedbackSource, /saved_feedback: true/);
  assert.doesNotMatch(postSavedFeedbackSource, /submitFeedback|"\/feedback"/);

  assert.match(feedbackHandlerSource, /postSavedFeedback\(item, feedbackType\)/);
  assert.match(savedCardSource, /handleSavedCardFeedback\(item, "like", like, dislike\)/);
  assert.match(savedCardSource, /handleSavedCardFeedback\(item, "dislike", dislike, like\)/);
  assert.match(savedCardSource, /postSavedFeedback\(item, "comment", note\)/);
  assert.match(behaviorApiSource, /requestJson\("\/events"/);
  assert.doesNotMatch(behaviorApiSource, /"\/feedback"/);
});

test("cross-list toggle reuses the matching shared binding without reloading", () => {
  const savedCardSource = sourceBetween(
    popupSource,
    "function buildSavedCard(",
    "\nfunction buildSavedCardMedia(",
  );

  assert.match(
    savedCardSource,
    /if \(crossIsFavorite\) return toggleSavedWithFeedback\("收藏", item, favoriteToggles, toggleFavoriteSaved\)/,
  );
  assert.match(
    savedCardSource,
    /return toggleSavedWithFeedback\("稍后再看", item, watchLaterToggles, toggleWatchLaterSaved\)/,
  );
  assert.match(savedCardSource, /if \(crossIsFavorite\) \{\s+bindFavoriteToggle\(crossToggle, item\)/);
  assert.match(savedCardSource, /else \{\s+bindWatchLaterToggle\(crossToggle, item\)/);
  assert.doesNotMatch(savedCardSource, /await onRemoved\(\)/);
  assert.doesNotMatch(savedCardSource, /const watchLater = createActionButton/);
  assert.doesNotMatch(savedCardSource, /const favorite = createActionButton/);
});
