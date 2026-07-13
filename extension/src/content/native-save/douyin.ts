import type { NativeSaveTask } from "../../shared/native-save.ts";

export interface DouyinSaveControl {
  isSelected(): boolean;
  click(): void;
}

export type DouyinFavoriteRequestResult = "success" | "rejected" | "rate_limited" | null;

export interface DouyinNativeSaveEnvironment {
  currentUrl: string;
  isLoggedIn(): boolean;
  isUnavailable(): boolean;
  rateLimitFingerprint(): string;
  requestFavorite(): Promise<DouyinFavoriteRequestResult>;
  findFavoriteControls(contentId: string): DouyinSaveControl[];
  sleep(ms: number): Promise<void>;
}

const CONFIRM_ATTEMPTS = 20;
const CONFIRM_INTERVAL_MS = 100;
const DOUYIN_CONTENT_IDENTITY_SELECTOR = "[data-aweme-id], [data-video-id], [data-content-id]";

function hasNewRateLimit(before: string, after: string): boolean {
  const baseline = new Set(before.split("\n").filter(Boolean));
  return after.split("\n").filter(Boolean).some((entry) => !baseline.has(entry));
}

function routeId(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port || url.hash) return null;
    if (url.hostname !== "douyin.com" && !url.hostname.endsWith(".douyin.com")) return null;
    return /^\/video\/([A-Za-z0-9_-]+)\/?$/.exec(url.pathname)?.[1] ?? null;
  } catch {
    return null;
  }
}

function isSupported(task: NativeSaveTask, currentUrl: string): boolean {
  return task.platform === "douyin" && task.platform_slug === "dy" &&
    task.item_key === `douyin:${task.content_id}` &&
    ["aweme", "video"].includes(task.content_type) && /^[A-Za-z0-9_-]+$/.test(task.content_id) &&
    routeId(task.content_url) === task.content_id && routeId(currentUrl) === task.content_id;
}

function hasTargetContract(task: NativeSaveTask): boolean {
  return task.target_label === "抖音收藏" && task.resolved_action === "favorite" &&
    (task.requested_action === "favorite" || task.requested_action === "watch_later");
}

async function confirmSelected(task: NativeSaveTask, env: DouyinNativeSaveEnvironment): Promise<boolean> {
  for (let attempt = 0; attempt < CONFIRM_ATTEMPTS; attempt += 1) {
    const controls = env.findFavoriteControls(task.content_id);
    if (controls.length === 1 && controls[0].isSelected()) return true;
    if (attempt + 1 < CONFIRM_ATTEMPTS) await env.sleep(CONFIRM_INTERVAL_MS);
  }
  return false;
}

export async function saveDouyin(
  task: NativeSaveTask,
  env: DouyinNativeSaveEnvironment = createDouyinBrowserEnvironment(),
): Promise<unknown> {
  if (!env.isLoggedIn()) return { status: "login_required" };
  if (!isSupported(task, env.currentUrl) || env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (!hasTargetContract(task)) return { status: "failed", error_code: "native_save_failed" };
  const initial = env.findFavoriteControls(task.content_id);
  if (initial.length !== 1) return { status: "failed", error_code: "native_save_failed" };
  if (initial[0].isSelected()) return { status: "already_synced" };
  const rateLimitBefore = env.rateLimitFingerprint();

  let requestResult: DouyinFavoriteRequestResult;
  try {
    requestResult = await env.requestFavorite();
  } catch {
    return { status: "failed", error_code: "native_save_failed" };
  }
  if (requestResult === "rate_limited") return { status: "rate_limited" };
  if (requestResult === "success") {
    if (await confirmSelected(task, env)) return { status: "synced" };
    return hasNewRateLimit(rateLimitBefore, env.rateLimitFingerprint())
      ? { status: "rate_limited" }
      : { status: "failed", error_code: "native_save_failed" };
  }

  const controls = env.findFavoriteControls(task.content_id);
  if (controls.length !== 1) return { status: "failed", error_code: "native_save_failed" };
  if (controls[0].isSelected()) return { status: "synced" };
  try {
    controls[0].click();
  } catch {
    return { status: "failed", error_code: "native_save_failed" };
  }
  if (await confirmSelected(task, env)) return { status: "synced" };
  return hasNewRateLimit(rateLimitBefore, env.rateLimitFingerprint())
    ? { status: "rate_limited" }
    : { status: "failed", error_code: "native_save_failed" };
}

function isEffectivelyVisible(element: HTMLElement, root: Document): boolean {
  const view = root.defaultView ?? element.ownerDocument?.defaultView;
  let current: HTMLElement | null = element;
  while (current) {
    if (
      current.hidden || current.hasAttribute("hidden") || current.hasAttribute("inert") ||
      current.getAttribute("aria-hidden") === "true" || current.style.display === "none" ||
      current.style.visibility === "hidden"
    ) return false;
    if (view) {
      const style = view.getComputedStyle(current);
      if (style.display === "none" || style.visibility === "hidden") return false;
    }
    current = current.parentElement;
  }
  return true;
}

function selected(element: HTMLElement): boolean {
  return element.getAttribute("aria-pressed") === "true" ||
    element.getAttribute("aria-checked") === "true" ||
    element.getAttribute("data-e2e-state") === "active" ||
    /(?:已收藏|取消收藏)/.test(element.getAttribute("aria-label") ?? element.title ?? element.textContent ?? "");
}

function isExactFavoriteControl(element: HTMLElement): boolean {
  if (element.getAttribute("data-e2e") === "video-favorite") return true;
  const label = (
    element.getAttribute("aria-label") ?? element.title ?? element.textContent ?? ""
  ).trim();
  return ["收藏", "已收藏", "取消收藏"].includes(label);
}

function douyinContentContainer(root: Document, contentId: string): HTMLElement | null {
  const candidates = Array.from(root.querySelectorAll<HTMLElement>(
    DOUYIN_CONTENT_IDENTITY_SELECTOR,
  )).filter((element) => {
    const ids = ["data-aweme-id", "data-video-id", "data-content-id"]
      .map((name) => element.getAttribute(name))
      .filter((value): value is string => value !== null);
    return ids.includes(contentId) && isEffectivelyVisible(element, root);
  });
  return candidates.length === 1 ? candidates[0] : null;
}

export function createDouyinBrowserEnvironment(
  root: Document = document,
  currentUrl: string = location.href,
): DouyinNativeSaveEnvironment {
  const rateElementIds = new WeakMap<Element, number>();
  let nextRateElementId = 1;
  return {
    currentUrl,
    isLoggedIn() {
      const overlays = root.querySelectorAll<HTMLElement>(
        "[role='dialog'] input[type='tel'], .login-modal, [data-e2e='login-modal']",
      );
      return !Array.from(overlays).some((element) => isEffectivelyVisible(element, root));
    },
    isUnavailable() {
      const errors = root.querySelectorAll<HTMLElement>(
        ".not-found, .error-page, [data-e2e='video-unavailable']",
      );
      return Array.from(errors).some((element) => isEffectivelyVisible(element, root));
    },
    rateLimitFingerprint() {
      const contentId = routeId(currentUrl);
      if (!contentId) return "";
      const container = douyinContentContainer(root, contentId);
      if (!container) return "";
      return Array.from(container.querySelectorAll<HTMLElement>("[role='alert'], [data-e2e='toast'], .toast"))
        .filter((element) =>
          element.closest(DOUYIN_CONTENT_IDENTITY_SELECTOR) === container &&
          isEffectivelyVisible(element, root)
        )
        .map((element) => {
          const text = element.textContent?.trim() ?? "";
          if (!/(?:操作频繁|请求频繁|稍后再试|risk control|too many requests|429)/i.test(text)) {
            return "";
          }
          let id = rateElementIds.get(element);
          if (id === undefined) {
            id = nextRateElementId;
            nextRateElementId += 1;
            rateElementIds.set(element, id);
          }
          return `${id}:${text}`;
        })
        .filter(Boolean)
        .join("\n");
    },
    async requestFavorite() {
      return null;
    },
    findFavoriteControls(contentId) {
      if (routeId(currentUrl) !== contentId) return [];
      const container = douyinContentContainer(root, contentId);
      if (!container) return [];
      return Array.from(container.querySelectorAll<HTMLElement>(
        "button[aria-label*='收藏'], [role='button'][aria-label*='收藏'], [data-e2e='video-favorite']",
      )).filter((element) =>
        element.closest(DOUYIN_CONTENT_IDENTITY_SELECTOR) === container &&
        isEffectivelyVisible(element, root) && isExactFavoriteControl(element)
      ).map((element) => ({
        isSelected: () => selected(element),
        click: () => element.click(),
      }));
    },
    sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    },
  };
}
