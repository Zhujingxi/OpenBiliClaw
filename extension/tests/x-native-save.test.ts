import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  createXBrowserEnvironment,
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
  readyAfterSleeps?: number;
} = {}): XNativeSaveEnvironment & { clicks: number; requestedIds: string[] } {
  let state = options.initialState ?? "bookmark";
  let sleeps = 0;
  const ready = () => sleeps >= (options.readyAfterSleeps ?? 0);
  const env = {
    clicks: 0,
    requestedIds: [] as string[],
    currentUrl: task.content_url,
    isLoggedIn: () => ready() && (options.loggedIn ?? true),
    isRateLimited: () => options.rateLimited ?? false,
    findTweetControl(tweetId: string, testId: "bookmark" | "removeBookmark"): XSaveControl | null {
      env.requestedIds.push(tweetId);
      if (!ready()) return null;
      if (state !== testId) return null;
      return {
        click() {
          env.clicks += 1;
          if (options.confirmAfterClick) state = "removeBookmark";
        },
      };
    },
    sleep: async () => { sleeps += 1; },
  } satisfies XNativeSaveEnvironment & { clicks: number; requestedIds: string[] };
  return env;
}

test("X native save clicks the exact tweet bookmark and confirms removeBookmark", async () => {
  const env = fixture({ confirmAfterClick: true });
  assert.deepEqual(await saveX(task, env), { status: "synced" });
  assert.equal(env.clicks, 1);
  assert.ok(env.requestedIds.every((id) => id === task.content_id));
});

test("X native save waits for the logged-in correlated tweet control", async () => {
  const env = fixture({ readyAfterSleeps: 2, confirmAfterClick: true });
  assert.deepEqual(await saveX(task, env), { status: "synced" });
  assert.equal(env.clicks, 1);
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

function browserDomFixture(options: {
  staleToast?: boolean;
  unrelatedStructuredError?: boolean;
  newRateToastAfterClick?: boolean;
  confirmAfterClick?: boolean;
  initialBookmarked?: boolean;
  targetRateAlert?: boolean;
} = {}): { document: Document; clicks: () => number } {
  let bookmarked = options.initialBookmarked ?? false;
  let clickCount = 0;
  const staleToast = { textContent: "请求过于频繁，请稍后再试" };
  const unrelatedError = { textContent: "Rate limit exceeded" };
  const newToast = { textContent: "操作频繁，请稍后再试" };
  const toasts: Array<{ textContent: string | null }> = options.staleToast ? [staleToast] : [];
  const bookmark = {
    click() {
      clickCount += 1;
      if (options.newRateToastAfterClick) toasts.push(newToast);
      if (options.confirmAfterClick ?? !options.newRateToastAfterClick) bookmarked = true;
    },
  };
  const article = {
    querySelector(selector: string) {
      if (selector === '[data-testid="bookmark"]') return bookmarked ? null : bookmark;
      if (selector === '[data-testid="removeBookmark"]') return bookmarked ? bookmark : null;
      return null;
    },
    querySelectorAll(selector: string) {
      return selector.includes("[role='alert']") && options.targetRateAlert
        ? [{ textContent: "Rate limit exceeded" }]
        : [];
    },
  };
  const statusLink = {
    href: task.content_url,
    closest(selector: string) {
      return selector === "article" ? article : null;
    },
  };
  const loggedIn = {};
  const document = {
    querySelector(selector: string) {
      if (selector.includes("SideNav_AccountSwitcher_Button")) return loggedIn;
      return null;
    },
    querySelectorAll(selector: string) {
      if (selector.startsWith('a[href*="/status/')) return [statusLink];
      if (selector === "[data-testid='toast']") return toasts;
      if (selector.includes("[role='alert'][data-testid]") || selector.includes("error-detail")) {
        return [
          ...toasts,
          ...(options.unrelatedStructuredError ? [unrelatedError] : []),
        ];
      }
      return [];
    },
  } as unknown as Document;
  return { document, clicks: () => clickCount };
}

test("X browser save ignores unrelated structured errors and stale global rate toasts", async () => {
  const dom = browserDomFixture({ staleToast: true, unrelatedStructuredError: true });
  const env = createXBrowserEnvironment(dom.document, task.content_url);
  env.sleep = async () => {};

  assert.deepEqual(await saveX(task, env), { status: "synced" });
  assert.equal(dom.clicks(), 1);
});

test("X browser save accepts a newly appeared platform rate toast after the target action", async () => {
  const dom = browserDomFixture({ newRateToastAfterClick: true });
  const env = createXBrowserEnvironment(dom.document, task.content_url);
  env.sleep = async () => {};

  assert.deepEqual(await saveX(task, env), { status: "rate_limited" });
  assert.equal(dom.clicks(), 1);
});

test("X browser save prefers initial removeBookmark proof over a stale target rate alert", async () => {
  const dom = browserDomFixture({ initialBookmarked: true, targetRateAlert: true });
  const env = createXBrowserEnvironment(dom.document, task.content_url);
  env.sleep = async () => {};

  assert.deepEqual(await saveX(task, env), { status: "already_synced" });
  assert.equal(dom.clicks(), 0);
});

test("X browser save prefers post-click removeBookmark proof over a new rate toast", async () => {
  const dom = browserDomFixture({ newRateToastAfterClick: true, confirmAfterClick: true });
  const env = createXBrowserEnvironment(dom.document, task.content_url);
  env.sleep = async () => {};

  assert.deepEqual(await saveX(task, env), { status: "synced" });
  assert.equal(dom.clicks(), 1);
});
