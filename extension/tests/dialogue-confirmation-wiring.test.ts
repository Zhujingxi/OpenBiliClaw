import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const extensionFile = (path: string) => readFileSync(resolve(path), "utf8");
const projectFile = (path: string) => readFileSync(resolve("..", path), "utf8");

test("popup wires pending list, card actions, and the shared renderer into the durable chat flow", () => {
  const api = extensionFile("popup/popup-api.js");
  const popup = extensionFile("popup/popup.js");
  const html = extensionFile("popup/popup.html");

  assert.match(api, /export async function fetchPendingConfirmations/);
  assert.match(api, /export async function openPendingConfirmation/);
  assert.match(api, /export async function actOnChatCard/);
  assert.match(popup, /OpenBiliClawDialogueConfirmation/);
  assert.match(popup, /selectDialogueTurns/);
  assert.match(popup, /executeCardAction/);
  assert.match(popup, /session:\s*CHAT_SESSION/);
  assert.match(html, /id="chatPendingToggle"/);
  assert.match(html, /id="chatPendingList"/);
  assert.match(html, /shared\/dialogue-confirmation\.js/);
});

test("service worker reuses its 30s flush alarm for a debounced count-only badge refresh", () => {
  const serviceWorker = extensionFile("src/background/service-worker.ts");

  assert.equal(
    (serviceWorker.match(/chrome\.alarms\.create\(/g) ?? []).length,
    1,
    "pending confirmations must not add another periodic alarm",
  );
  assert.match(serviceWorker, /BUFFER_FLUSH_INTERVAL\s*=\s*30_000/);
  assert.match(serviceWorker, /pending-confirmations\?count_only=1/);
  assert.match(serviceWorker, /schedulePendingConfirmationBadgeRefresh/);
  assert.match(serviceWorker, /refreshPendingConfirmationBadge/);
  assert.match(serviceWorker, /if \(backendReachable !== true \|\| backendUninitialized\)/);
});

test("desktop mirrors popup semantics with webui session and a visible pending count", () => {
  const app = projectFile("src/openbiliclaw/web/desktop/assets/js/app.js");
  const html = projectFile("src/openbiliclaw/web/desktop/index.html");

  assert.match(app, /pendingConfirmations:\s*"\/chat\/pending-confirmations"/);
  assert.match(app, /OpenBiliClawDialogueConfirmation/);
  assert.match(app, /executeCardAction/);
  assert.match(app, /session:\s*"webui"/);
  assert.match(html, /id="chatPendingCountBadge"/);
  assert.match(html, /id="desktopPendingConfirmations"/);
  assert.match(html, /\/shared\/dialogue-confirmation\.js/);
});

test("mobile active insights are read-only in Wave C", () => {
  const profile = projectFile("src/openbiliclaw/web/js/views/profile.js");

  assert.doesNotMatch(profile, /submitInsightFeedback/);
  assert.doesNotMatch(profile, /bindInsightActions/);
  assert.doesNotMatch(profile, /data-insight-idx/);
  assert.match(profile, /insight-readonly/);
  assert.match(profile, /请在插件或桌面端的对话入口确认/);
});
