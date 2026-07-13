import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  hasExplicitXRateLimitText,
  saveX,
  type XNativeSaveEnvironment,
  type XSaveControl,
} from "../src/content/native-save/x.ts";
import type { NativeSaveTask } from "../src/shared/native-save.ts";

const task: NativeSaveTask = {
  id: "123e4567-e89b-42d3-a456-426614174002",
  type: "native_save",
  platform: "twitter",
  platform_slug: "x",
  item_key: "twitter:1234567890",
  content_id: "1234567890",
  content_url: "https://x.com/i/status/1234567890",
  content_type: "tweet",
  requested_action: "favorite",
  resolved_action: "favorite",
  target_label: "X Bookmarks",
};

function fixture(options: {
  loggedIn?: boolean;
  rateLimited?: boolean;
  initialState?: "bookmark" | "removeBookmark" | null;
  confirmAfterClick?: boolean;
} = {}): XNativeSaveEnvironment & { clicks: number; requestedIds: string[] } {
  let state = options.initialState ?? "bookmark";
  const env = {
    clicks: 0,
    requestedIds: [] as string[],
    currentUrl: task.content_url,
    isLoggedIn: () => options.loggedIn ?? true,
    isRateLimited: () => options.rateLimited ?? false,
    findTweetControl(tweetId: string, testId: "bookmark" | "removeBookmark"): XSaveControl | null {
      env.requestedIds.push(tweetId);
      if (state !== testId) return null;
      return {
        click() {
          env.clicks += 1;
          if (options.confirmAfterClick) state = "removeBookmark";
        },
      };
    },
    sleep: async () => {},
  } satisfies XNativeSaveEnvironment & { clicks: number; requestedIds: string[] };
  return env;
}

test("X native save clicks the exact tweet bookmark and confirms removeBookmark", async () => {
  const env = fixture({ confirmAfterClick: true });
  assert.deepEqual(await saveX(task, env), { status: "synced" });
  assert.equal(env.clicks, 1);
  assert.ok(env.requestedIds.every((id) => id === task.content_id));
});

test("X native save returns already_synced for removeBookmark without mutation", async () => {
  const env = fixture({ initialState: "removeBookmark" });
  assert.deepEqual(await saveX(task, env), { status: "already_synced" });
  assert.equal(env.clicks, 0);
});

test("X native save maps login, rate control, unsupported identities, and missing confirmation", async () => {
  const loggedOut = fixture({ loggedIn: false });
  loggedOut.currentUrl = "https://x.com/i/flow/login";
  assert.deepEqual(await saveX(task, loggedOut), { status: "login_required" });
  assert.deepEqual(await saveX(task, fixture({ rateLimited: true })), { status: "rate_limited" });
  for (const unsupported of [
    { ...task, content_id: "user-alice", item_key: "twitter:user-alice", content_type: "user", content_url: "https://x.com/alice" },
    { ...task, content_id: "list-123", item_key: "twitter:list-123", content_type: "list", content_url: "https://x.com/i/lists/123" },
  ]) {
    assert.deepEqual(await saveX(unsupported, fixture()), {
      status: "unsupported",
      error_code: "unsupported_content_type",
    });
  }
  assert.deepEqual(await saveX(task, fixture()), {
    status: "failed",
    error_code: "native_save_failed",
  });
});

test("watch-later fallback uses the same X Bookmark mutation once", async () => {
  const env = fixture({ confirmAfterClick: true });
  assert.deepEqual(await saveX({ ...task, requested_action: "watch_later" }, env), { status: "synced" });
  assert.equal(env.clicks, 1);
});

test("X rate detection ignores tweet prose and recognizes structured localized risk alerts", () => {
  assert.equal(hasExplicitXRateLimitText([]), false);
  assert.equal(hasExplicitXRateLimitText([{ textContent: "This tweet says: try again later" }]), true);
  assert.equal(hasExplicitXRateLimitText([{ textContent: "请求过于频繁，请稍后再试" }]), true);
  const source = readFileSync(resolve("src/content/native-save/x.ts"), "utf8");
  assert.doesNotMatch(source, /document\.body\?\.innerText|document\.body\.innerText/);
  assert.match(source, /data-testid=['"]toast|role=['"]alert/);
});
