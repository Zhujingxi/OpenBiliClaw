import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function sourceBlock(source: string, start: string, end: string): string {
  const startIndex = source.indexOf(start);
  assert.notEqual(startIndex, -1, `missing start marker: ${start}`);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(endIndex, -1, `missing end marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

test("mobile stream removes only negative delight feedback", () => {
  const recommendJs = readFileSync(
    resolve("../src/openbiliclaw/web/js/views/recommend.js"),
    "utf8",
  );
  const chatJs = readFileSync(
    resolve("../src/openbiliclaw/web/js/views/chat.js"),
    "utf8",
  );

  assert.doesNotMatch(
    recommendJs,
    /type === "delight\.liked"\s*\|\|\s*type === "delight\.disliked"/,
  );
  assert.match(recommendJs, /type === "delight\.disliked"/);
  assert.doesNotMatch(
    chatJs,
    /type === "delight\.liked"\s*\|\|\s*type === "delight\.disliked"/,
  );
  assert.doesNotMatch(
    chatJs,
    /if \(scope === "delight"\) \{\s*delightMsgs = delightMsgs\.filter/,
  );
  assert.match(chatJs, /if \(permanent\) \{\s*markDelightSent[\s\S]*?delightMsgs = delightMsgs\.filter/);
});

test("extension delight banner keeps positive actions visible", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const openBlock = sourceBlock(
    popupJs,
    "const openButton = createActionButton(",
    "const likeButton = createActionButton(",
  );
  const likeBlock = sourceBlock(
    popupJs,
    "const likeButton = createActionButton(",
    "const rejectButton = createActionButton(",
  );
  const rejectBlock = sourceBlock(
    popupJs,
    "const rejectButton = createActionButton(",
    "const chatButton = createActionButton(",
  );

  assert.doesNotMatch(openBlock, /shiftDelightQueue|removeCurrentDelight/);
  assert.doesNotMatch(likeBlock, /shiftDelightQueue|removeCurrentDelight|rememberDismissedDelight/);
  assert.match(rejectBlock, /removeCurrentDelight/);
});

test("extension delight close persists handled content as seen", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const rememberBlock = sourceBlock(
    popupJs,
    "function rememberDismissedDelight(bvid)",
    "// ── Delight queue helpers",
  );
  const dismissBlock = sourceBlock(
    popupJs,
    'dismiss.className = "delight-banner-dismiss";',
    "banner.append(row, dismiss);",
  );

  assert.match(rememberBlock, /respondToDelight\(bvid, "dismiss"\)/);
  assert.doesNotMatch(rememberBlock, /markDelightSent/);
  assert.match(dismissBlock, /aria-label", "看过了，不再推荐"/);
  assert.match(dismissBlock, /await rememberDismissedDelight\(delight\.bvid\)/);
});

test("desktop delight actions remove only explicit negative responses", () => {
  const desktopJs = readFileSync(
    resolve("../src/openbiliclaw/web/desktop/assets/js/app.js"),
    "utf8",
  );
  const responseBlock = sourceBlock(
    desktopJs,
    "const feedbackToast = response === \"like\"",
    "function openMessageChat(msg)",
  );

  assert.match(
    responseBlock,
    /if \(response === "dislike" \|\| response === "dismiss"\) \{\s*state\.delights = state\.delights\.filter/,
  );
});
