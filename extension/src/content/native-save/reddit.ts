import type { NativeSaveTask } from "../../shared/native-save.ts";
import { waitForNativeSaveReadiness } from "./readiness.ts";

export interface RedditSaveControl {
  click(): void;
}

type RedditSavedState = "saved" | "unsaved" | "unknown";

export interface RedditNativeSaveEnvironment {
  currentUrl: string;
  isLoggedIn(): boolean;
  requestToken(): string | null;
  postSave(body: URLSearchParams): Promise<{ ok: boolean; status: number }>;
  fetchSavedState(fullname: string): Promise<RedditSavedState>;
  findControl(fullname: string, label: "Save" | "Unsave"): RedditSaveControl | null;
  sleep(ms: number): Promise<void>;
}

const CONFIRM_ATTEMPTS = 20;
const CONFIRM_INTERVAL_MS = 100;

type RedditIdentityCorrelation = "url" | "dom_required";

function supportedIdentity(task: NativeSaveTask, currentUrl: string): RedditIdentityCorrelation | null {
  const match = /^(t[13])_([a-z0-9]+)$/i.exec(task.content_id);
  if (!match) return null;
  if (match[1] === "t3" && task.content_type !== "post") return null;
  if (match[1] === "t1" && task.content_type !== "comment") return null;
  try {
    const route = (value: string): { exact: boolean; postId: string | null } => {
      const url = new URL(value);
      const segments = url.pathname.toLowerCase().split("/").filter(Boolean);
      const id = match[2].toLowerCase();
      if (match[1] === "t3" && (url.hostname === "redd.it" || url.hostname.endsWith(".redd.it"))) {
        return { exact: segments.length === 1 && segments[0] === id, postId: id };
      }
      const commentsIndex = segments.indexOf("comments");
      const postId = segments[commentsIndex + 1] ?? null;
      if (commentsIndex < 0 || !postId) return { exact: false, postId: null };
      return {
        exact: match[1] === "t3"
          ? postId === id
          : segments[commentsIndex + 3] === id,
        postId,
      };
    };
    const taskRoute = route(task.content_url);
    if (!taskRoute.exact) return null;
    const currentRoute = route(currentUrl);
    if (currentRoute.exact) return "url";
    if (match[1] === "t1" && currentRoute.postId === taskRoute.postId) return "dom_required";
    return null;
  } catch {
    return null;
  }
}

async function confirmSaved(task: NativeSaveTask, env: RedditNativeSaveEnvironment): Promise<boolean> {
  for (let attempt = 0; attempt < CONFIRM_ATTEMPTS; attempt += 1) {
    if (env.findControl(task.content_id, "Unsave")) return true;
    if (await env.fetchSavedState(task.content_id) === "saved") return true;
    if (attempt + 1 < CONFIRM_ATTEMPTS) await env.sleep(CONFIRM_INTERVAL_MS);
  }
  return false;
}

export async function saveReddit(
  task: NativeSaveTask,
  env: RedditNativeSaveEnvironment = browserRedditEnvironment(),
): Promise<unknown> {
  if (!env.isLoggedIn() && /^\/login(?:\/|$)/.test(new URL(env.currentUrl).pathname)) {
    return { status: "login_required" };
  }
  const identityCorrelation = supportedIdentity(task, env.currentUrl);
  if (!identityCorrelation) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  await waitForNativeSaveReadiness(
    () => env.isLoggedIn() && Boolean(
      env.requestToken() || env.findControl(task.content_id, "Save") ||
      env.findControl(task.content_id, "Unsave"),
    ),
    env.sleep,
  );
  if (!env.isLoggedIn()) return { status: "login_required" };
  if (env.findControl(task.content_id, "Unsave")) return { status: "already_synced" };
  let saveControl = env.findControl(task.content_id, "Save");
  if (identityCorrelation === "dom_required" && !saveControl) {
    return { status: "failed", error_code: "native_save_failed" };
  }

  const token = env.requestToken();
  if (token && await env.fetchSavedState(task.content_id) === "saved") {
    return { status: "already_synced" };
  }
  if (token) {
    let response: { ok: boolean; status: number };
    try {
      const body = new URLSearchParams({ id: task.content_id, uh: token, api_type: "json" });
      response = await env.postSave(body);
    } catch {
      return await confirmSaved(task, env)
        ? { status: "synced" }
        : { status: "failed", error_code: "native_confirmation_not_observed" };
    }
    if (response.status === 429) return { status: "rate_limited" };
    if (response.ok) {
      return await confirmSaved(task, env)
        ? { status: "synced" }
        : { status: "failed", error_code: "native_confirmation_not_observed" };
    }
    if (response.status !== 403) return { status: "failed", error_code: "native_save_failed" };
  }

  saveControl ??= env.findControl(task.content_id, "Save");
  if (!saveControl) return { status: "failed", error_code: "native_save_failed" };
  try {
    saveControl.click();
  } catch {
    return { status: "failed", error_code: "native_save_failed" };
  }
  return await confirmSaved(task, env)
    ? { status: "synced" }
    : { status: "failed", error_code: "native_confirmation_not_observed" };
}

function queryAllOpenShadowRoots(root: ParentNode, selector: string): HTMLElement[] {
  const candidates = Array.from(root.querySelectorAll<HTMLElement>(selector));
  const shadowRoots = new Set<ShadowRoot>();
  const ownShadowRoot = (root as Element & { shadowRoot?: ShadowRoot | null }).shadowRoot;
  if (ownShadowRoot) shadowRoots.add(ownShadowRoot);
  for (const element of Array.from(root.querySelectorAll<HTMLElement>("*"))) {
    if (element.shadowRoot) shadowRoots.add(element.shadowRoot);
  }
  for (const shadowRoot of shadowRoots) {
    candidates.push(...queryAllOpenShadowRoots(shadowRoot, selector));
  }
  return candidates;
}

function isRedditIdentityNode(value: unknown): boolean {
  if (typeof value !== "object" || value === null) return false;
  const node = value as {
    tagName?: unknown;
    matches?: (selector: string) => boolean;
    getAttribute?: (name: string) => string | null;
  };
  try {
    if (node.matches?.("[data-fullname], [thingid], shreddit-post, shreddit-comment")) return true;
  } catch {
    // Fall through to attribute/tag checks for minimal DOM implementations.
  }
  const tagName = typeof node.tagName === "string" ? node.tagName.toLowerCase() : "";
  return tagName === "shreddit-post"
    || tagName === "shreddit-comment"
    || node.getAttribute?.("data-fullname") !== null && node.getAttribute?.("data-fullname") !== undefined
    || node.getAttribute?.("thingid") !== null && node.getAttribute?.("thingid") !== undefined;
}

function composedParent(value: unknown): unknown {
  if (typeof value !== "object" || value === null) return null;
  const node = value as {
    parentNode?: unknown;
    host?: unknown;
    getRootNode?: () => unknown;
  };
  if (node.parentNode) return node.parentNode;
  if (node.host) return node.host;
  try {
    const tree = node.getRootNode?.() as { host?: unknown } | undefined;
    if (tree && tree !== value && tree.host) return tree.host;
  } catch {
    // Missing composed-tree metadata fails closed below when ancestry was observable.
  }
  return null;
}

function belongsToExactIdentityRoot(element: HTMLElement, root: ParentNode): boolean {
  let current: unknown = element;
  let observedAncestry = false;
  for (let depth = 0; depth < 128 && current; depth += 1) {
    if (current === root) return true;
    if (current !== element && isRedditIdentityNode(current)) return false;
    const parent = composedParent(current);
    if (!parent) return !observedAncestry;
    observedAncestry = true;
    current = parent;
  }
  return false;
}

function exactControl(root: ParentNode, label: "Save" | "Unsave"): HTMLElement | null {
  const candidates = queryAllOpenShadowRoots(root, "button, a, [role='button']");
  const matches = candidates.filter((element) => {
    const text = element.textContent?.trim() ?? "";
    const aria = element.getAttribute("aria-label")?.trim() ?? "";
    return (text === label || aria === label) && belongsToExactIdentityRoot(element, root);
  });
  return matches.length === 1 ? matches[0] : null;
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
    async fetchSavedState(fullname) {
      try {
        const url = new URL("/api/info.json", location.origin);
        url.searchParams.set("id", fullname);
        url.searchParams.set("raw_json", "1");
        const response = await fetch(url, { credentials: "include" });
        if (!response.ok) return "unknown";
        const payload = await response.json() as {
          data?: { children?: Array<{ data?: { name?: unknown; saved?: unknown } }> };
        };
        const children = payload.data?.children;
        if (!Array.isArray(children) || children.length !== 1) return "unknown";
        const item = children[0]?.data;
        if (item?.name !== fullname || typeof item.saved !== "boolean") return "unknown";
        return item.saved ? "saved" : "unsaved";
      } catch {
        return "unknown";
      }
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
