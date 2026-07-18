import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createServer } from "node:http";

import {
  __resetBackendEndpointForTests,
  updateBackendEndpoint,
} from "../popup/popup-backend-config.js";
import {
  fetchSavedItems,
  removeSavedItem,
  saveItem,
  savedItemStatus,
} from "../popup/popup-api.js";
import * as popupApi from "../popup/popup-api.js";

function jsonResponse(res, status, payload) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(payload));
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(chunk);
  }
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function makeItem(itemKey, listKind) {
  const [sourcePlatform, contentId] = itemKey.split(":", 2);
  return {
    item_key: itemKey,
    content_id: contentId,
    title: `${listKind} ${contentId}`,
    author_name: "测试 UP",
    cover_url: "",
    content_url: `https://www.bilibili.com/video/${contentId}`,
    source_platform: sourcePlatform,
    added_at: "2026-05-31T12:00:00",
  };
}

function savedItemInput(contentId) {
  return {
    source_platform: "bilibili",
    content_id: contentId,
    content_url: `https://www.bilibili.com/video/${contentId}`,
    title: `video ${contentId}`,
    up_name: "测试 UP",
  };
}

async function startSavedBackend() {
  const store = {
    watch_later: new Map(),
    favorite: new Map(),
  };
  const requests = [];
  const server = createServer(async (req, res) => {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    requests.push(`${req.method} ${url.pathname}${url.search}`);
    const match = url.pathname.match(/^\/api\/saved\/(watch_later|favorite)(\/remove|\/status)?$/);
    if (!match) {
      jsonResponse(res, 404, { error: "not_found" });
      return;
    }

    const [, listKind, action] = match;
    const map = store[listKind];

    if (req.method === "POST" && !action) {
      const body = await readJson(req);
      const itemKey = `${body.source_platform}:${body.content_id}`;
      map.set(itemKey, body);
      jsonResponse(res, 200, { ok: true, item_key: itemKey });
      return;
    }
    if (req.method === "POST" && action === "/remove") {
      const body = await readJson(req);
      map.delete(String(body.item_key || ""));
      jsonResponse(res, 200, { ok: true });
      return;
    }
    if (req.method === "GET" && action === "/status") {
      const itemKey = url.searchParams.get("item_key") || "";
      jsonResponse(res, 200, { item_key: itemKey, saved: map.has(itemKey) });
      return;
    }
    if (req.method === "GET" && !action) {
      jsonResponse(res, 200, {
        items: Array.from(map.keys()).map((key) => makeItem(key, listKind)),
        total: map.size,
      });
      return;
    }

    jsonResponse(res, 405, { error: "method_not_allowed" });
  });

  await new Promise((resolveListen) => {
    server.listen(0, "127.0.0.1", resolveListen);
  });
  return { server, port: server.address().port, requests };
}

test("popup saved surfaces round-trip through the platform-neutral saved API", async () => {
  const { server, port, requests } = await startSavedBackend();
  __resetBackendEndpointForTests();
  await updateBackendEndpoint("http", "127.0.0.1", port);

  try {
    assert.deepEqual(await savedItemStatus("watch_later", "bilibili:BV1E2E"), {
      item_key: "bilibili:BV1E2E",
      saved: false,
    });

    await saveItem("watch_later", savedItemInput("BV1E2E"));
    await saveItem("favorite", savedItemInput("BV1E2E"));
    assert.equal((await savedItemStatus("watch_later", "bilibili:BV1E2E")).saved, true);
    assert.equal((await savedItemStatus("favorite", "bilibili:BV1E2E")).saved, true);
    assert.equal((await fetchSavedItems("watch_later")).items[0].item_key, "bilibili:BV1E2E");
    assert.equal((await fetchSavedItems("favorite")).items[0].item_key, "bilibili:BV1E2E");

    await removeSavedItem("watch_later", "bilibili:BV1E2E");
    assert.equal((await savedItemStatus("watch_later", "bilibili:BV1E2E")).saved, false);
    assert.equal((await savedItemStatus("favorite", "bilibili:BV1E2E")).saved, true);

    await removeSavedItem("favorite", "bilibili:BV1E2E");
    assert.equal((await savedItemStatus("favorite", "bilibili:BV1E2E")).saved, false);

    // Every call went to the canonical /api/saved/* routes — never the legacy
    // Bilibili-only /api/watch-later or /api/favorites endpoints.
    assert.ok(requests.length > 0);
    assert.ok(requests.every((line) => line.includes("/api/saved/")));
    assert.ok(requests.every((line) => !line.includes("/api/watch-later")));
    assert.ok(requests.every((line) => !line.includes("/api/favorites")));

    const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
    const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
    const popupSavedSync = readFileSync(resolve("popup", "popup-saved-sync.js"), "utf8");
    assert.match(popupHtml, /id="tabWatchLater"/);
    assert.match(popupHtml, /id="viewWatchLater"/);
    assert.match(popupHtml, /id="watchLaterList"/);
    assert.match(popupHtml, /id="tabFavorites"/);
    assert.match(popupHtml, /id="viewFavorites"/);
    assert.match(popupHtml, /id="favoritesList"/);
    assert.match(popupJs, /function loadWatchLater/);
    assert.match(popupJs, /function loadFavorites/);
    assert.match(popupJs, /toggleSavedWithFeedback\("稍后再看", item/);
    assert.match(popupJs, /toggleSavedWithFeedback\("收藏", item/);
    // Saved-card removal must stay optimistic (remove first, restore + 重试 on
    // failure) — the old await-then-remove flow read as "clicking does nothing"
    // whenever the DELETE was slow or failed.
    assert.match(popupJs, /function bindSavedCardRemove/);
    assert.match(popupJs, /remove\.textContent = "重试"/);
    assert.match(popupSavedSync, /unsupported_content_type/);
    assert.match(popupSavedSync, /unsupported_adapter_missing/);
    assert.match(popupSavedSync, /请连接已安装 OpenBiliClaw 插件的登录态浏览器后重试/);
    assert.match(popupJs, /aria-disabled/);
    assert.match(popupJs, /sync_status[^\n]*(pending|syncing)/);
    assert.match(popupHtml, /id="watchLaterSyncAll"/);
    assert.match(popupHtml, /id="favoritesSyncAll"/);
    assert.match(popupHtml, /id="cfgSavedAutoSync"[^>]*type="checkbox"/);
  } finally {
    __resetBackendEndpointForTests();
    await new Promise((resolveClose) => server.close(resolveClose));
  }
});

test("popup-api exposes no legacy bilibili saved exports", () => {
  for (const name of [
    "addToWatchLater",
    "removeFromWatchLater",
    "watchLaterStatus",
    "fetchWatchLater",
    "addToFavorite",
    "removeFromFavorite",
    "favoriteStatus",
    "fetchFavorites",
  ]) {
    assert.equal(
      typeof (popupApi as Record<string, unknown>)[name],
      "undefined",
      `legacy export ${name} should be removed`,
    );
  }
});
