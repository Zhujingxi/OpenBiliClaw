import type { NativeSaveTask } from "../../shared/native-save.ts";

export interface RedditSaveControl {
  click(): void;
}

export interface RedditNativeSaveEnvironment {
  currentUrl: string;
  isLoggedIn(): boolean;
  requestToken(): string | null;
  postSave(body: URLSearchParams): Promise<{ ok: boolean; status: number }>;
  findControl(fullname: string, label: "Save" | "Unsave"): RedditSaveControl | null;
  sleep(ms: number): Promise<void>;
}

const CONFIRM_ATTEMPTS = 20;
const CONFIRM_INTERVAL_MS = 100;

function supportedIdentity(task: NativeSaveTask, currentUrl: string): boolean {
  const match = /^(t[13])_([a-z0-9]+)$/i.exec(task.content_id);
  if (!match) return false;
  if (match[1] === "t3" && task.content_type !== "post") return false;
  if (match[1] === "t1" && task.content_type !== "comment") return false;
  try {
    const hasIdentity = (value: string): boolean => {
      const url = new URL(value);
      const segments = url.pathname.toLowerCase().split("/").filter(Boolean);
      const id = match[2].toLowerCase();
      if (match[1] === "t3" && (url.hostname === "redd.it" || url.hostname.endsWith(".redd.it"))) {
        return segments.length === 1 && segments[0] === id;
      }
      const commentsIndex = segments.indexOf("comments");
      if (commentsIndex < 0 || segments[commentsIndex + 1] === undefined) return false;
      return match[1] === "t3"
        ? segments[commentsIndex + 1] === id
        : segments.slice(commentsIndex + 2).includes(id);
    };
    return hasIdentity(task.content_url) && hasIdentity(currentUrl);
  } catch {
    return false;
  }
}

async function confirmSaved(task: NativeSaveTask, env: RedditNativeSaveEnvironment): Promise<boolean> {
  for (let attempt = 0; attempt < CONFIRM_ATTEMPTS; attempt += 1) {
    if (env.findControl(task.content_id, "Unsave")) return true;
    if (attempt + 1 < CONFIRM_ATTEMPTS) await env.sleep(CONFIRM_INTERVAL_MS);
  }
  return false;
}

export async function saveReddit(
  task: NativeSaveTask,
  env: RedditNativeSaveEnvironment = browserRedditEnvironment(),
): Promise<unknown> {
  if (!env.isLoggedIn()) return { status: "login_required" };
  if (!supportedIdentity(task, env.currentUrl)) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (env.findControl(task.content_id, "Unsave")) return { status: "already_synced" };

  const token = env.requestToken();
  if (token) {
    try {
      const body = new URLSearchParams({ id: task.content_id, uh: token, api_type: "json" });
      const response = await env.postSave(body);
      if (response.status === 429) return { status: "rate_limited" };
      if (response.ok && await confirmSaved(task, env)) return { status: "synced" };
    } catch {
      // A request rejected before confirmation may still use the visible control.
    }
  }

  const saveControl = env.findControl(task.content_id, "Save");
  if (!saveControl) return { status: "failed", error_code: "native_save_failed" };
  try {
    saveControl.click();
  } catch {
    return { status: "failed", error_code: "native_save_failed" };
  }
  return await confirmSaved(task, env)
    ? { status: "synced" }
    : { status: "failed", error_code: "native_save_failed" };
}

function exactControl(root: ParentNode, label: "Save" | "Unsave"): HTMLElement | null {
  const candidates = Array.from(root.querySelectorAll<HTMLElement>("button, a, [role='button']"));
  return candidates.find((element) => {
    const text = element.textContent?.trim() ?? "";
    const aria = element.getAttribute("aria-label")?.trim() ?? "";
    return text === label || aria === label;
  }) ?? null;
}

function targetRoot(fullname: string): ParentNode | null {
  const bareId = fullname.slice(3);
  const selectors = [
    `[data-fullname="${fullname}"]`,
    `[thingid="${fullname}"]`,
    `#thing_${fullname}`,
    `shreddit-post[id="${bareId}"]`,
    `shreddit-comment[thingid="${fullname}"]`,
  ];
  for (const selector of selectors) {
    const root = document.querySelector(selector);
    if (root) return root;
  }
  return null;
}

function browserRedditEnvironment(): RedditNativeSaveEnvironment {
  return {
    currentUrl: location.href,
    isLoggedIn() {
      if (/^\/login(?:\/|$)/.test(location.pathname)) return false;
      if (document.querySelector("[data-testid='login-button'], a[href*='/login']")) return false;
      return Boolean(document.querySelector(
        "[data-testid='user-menu'], button[aria-label*='user menu' i], form.logout, a[href*='/user/']",
      ));
    },
    requestToken() {
      const input = document.querySelector<HTMLInputElement>("input[name='uh']");
      const token = input?.value.trim() ?? "";
      return token || null;
    },
    async postSave(body) {
      const response = await fetch(new URL("/api/save", location.origin), {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/x-www-form-urlencoded;charset=UTF-8" },
        body,
      });
      return { ok: response.ok, status: response.status };
    },
    findControl(fullname, label) {
      const root = targetRoot(fullname);
      return root ? exactControl(root, label) : null;
    },
    sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    },
  };
}
