import type { NativeSaveTask } from "../../shared/native-save.ts";

export interface XSaveControl {
  click(): void;
}

export interface XNativeSaveEnvironment {
  currentUrl: string;
  isLoggedIn(): boolean;
  isRateLimited(): boolean;
  findTweetControl(tweetId: string, testId: "bookmark" | "removeBookmark"): XSaveControl | null;
  sleep(ms: number): Promise<void>;
}

const CONFIRM_ATTEMPTS = 20;
const CONFIRM_INTERVAL_MS = 100;

function supportedTweet(task: NativeSaveTask, currentUrl: string): boolean {
  if (!/^\d+$/.test(task.content_id) || !["tweet", "status"].includes(task.content_type)) return false;
  try {
    const taskPath = new URL(task.content_url).pathname;
    const currentPath = new URL(currentUrl).pathname;
    const expected = task.content_id;
    const validPath = (path: string): boolean =>
      path === `/i/status/${expected}` || new RegExp(`^/[^/]+/status/${expected}/?$`).test(path);
    return validPath(taskPath) && validPath(currentPath);
  } catch {
    return false;
  }
}

async function confirmBookmarked(task: NativeSaveTask, env: XNativeSaveEnvironment): Promise<boolean> {
  for (let attempt = 0; attempt < CONFIRM_ATTEMPTS; attempt += 1) {
    if (env.isRateLimited()) return false;
    if (env.findTweetControl(task.content_id, "removeBookmark")) return true;
    if (attempt + 1 < CONFIRM_ATTEMPTS) await env.sleep(CONFIRM_INTERVAL_MS);
  }
  return false;
}

export async function saveX(
  task: NativeSaveTask,
  env: XNativeSaveEnvironment = browserXEnvironment(),
): Promise<unknown> {
  if (!env.isLoggedIn()) return { status: "login_required" };
  if (!supportedTweet(task, env.currentUrl)) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (env.isRateLimited()) return { status: "rate_limited" };
  if (env.findTweetControl(task.content_id, "removeBookmark")) return { status: "already_synced" };
  const control = env.findTweetControl(task.content_id, "bookmark");
  if (!control) return { status: "failed", error_code: "native_save_failed" };
  try {
    control.click();
  } catch {
    return { status: "failed", error_code: "native_save_failed" };
  }
  if (await confirmBookmarked(task, env)) return { status: "synced" };
  return env.isRateLimited()
    ? { status: "rate_limited" }
    : { status: "failed", error_code: "native_save_failed" };
}

function tweetArticle(tweetId: string): HTMLElement | null {
  const links = Array.from(document.querySelectorAll<HTMLAnchorElement>(`a[href*="/status/${tweetId}"]`));
  const exactLink = links.find((link) => {
    try {
      const path = new URL(link.href, location.href).pathname;
      return path === `/i/status/${tweetId}` || new RegExp(`^/[^/]+/status/${tweetId}/?$`).test(path);
    } catch {
      return false;
    }
  });
  return exactLink?.closest<HTMLElement>("article") ?? null;
}

function browserXEnvironment(): XNativeSaveEnvironment {
  return {
    currentUrl: location.href,
    isLoggedIn() {
      if (/^\/i\/flow\/login/.test(location.pathname)) return false;
      if (document.querySelector("a[href='/login'], [data-testid='loginButton']")) return false;
      return Boolean(document.querySelector("[data-testid='SideNav_AccountSwitcher_Button'], a[href='/home']"));
    },
    isRateLimited() {
      const text = document.body?.innerText ?? "";
      return /rate limit|too many requests|try again later|temporarily limited/i.test(text);
    },
    findTweetControl(tweetId, testId) {
      return tweetArticle(tweetId)?.querySelector<HTMLElement>(`[data-testid="${testId}"]`) ?? null;
    },
    sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    },
  };
}
