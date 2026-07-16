import type { NativeSaveTask } from "../../shared/native-save.ts";
import { waitForNativeSaveReadiness } from "./readiness.ts";

export interface DouyinSaveControl {
  isSelected(): boolean;
  click(): void;
}

export type DouyinFavoriteRequestResult = "success" | "rejected" | "rate_limited" | null;

export interface DouyinNativeSaveEnvironment {
  currentUrl: string;
  isLoggedIn(): boolean;
  isUnavailable(): boolean;
  isContentReady(): boolean;
  rateLimitFingerprint(): string;
  requestFavorite(): Promise<DouyinFavoriteRequestResult>;
  findFavoriteControls(contentId: string): DouyinSaveControl[];
  waitForAccountState(): Promise<void>;
  sleep(ms: number): Promise<void>;
}

// Real modal E2E (2026-07-14) kept the collection Lottie mounted beyond 10s;
// allow 20s for the hydrated selected modifier, without issuing another click.
const CONFIRM_ATTEMPTS = 200;
const CONFIRM_INTERVAL_MS = 100;
const STATE_SETTLE_MS = 15_000;
const DOUYIN_CONTENT_IDENTITY_SELECTOR = "[data-aweme-id], [data-video-id], [data-content-id]";
const DOUYIN_CONTROL_OWNER_SELECTOR =
  `${DOUYIN_CONTENT_IDENTITY_SELECTOR}, [data-e2e='feed-active-video'], [data-e2e='feed-video']`;

function hasNewRateLimit(before: string, after: string): boolean {
  const baseline = new Set(before.split("\n").filter(Boolean));
  return after.split("\n").filter(Boolean).some((entry) => !baseline.has(entry));
}

function routeId(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port || url.hash) return null;
    if (url.hostname !== "douyin.com" && !url.hostname.endsWith(".douyin.com")) return null;
    const videoId = /^\/video\/([A-Za-z0-9_-]+)\/?$/.exec(url.pathname)?.[1];
    if (videoId) return url.search === "" ? videoId : null;
    if (!/^\/jingxuan\/?$/.test(url.pathname)) return null;
    const modalIds = url.searchParams.getAll("modal_id");
    let hasOnlyModalId = true;
    url.searchParams.forEach((_item, key) => {
      if (key !== "modal_id") hasOnlyModalId = false;
    });
    return hasOnlyModalId && modalIds.length === 1 && /^[A-Za-z0-9_-]+$/.test(modalIds[0])
      ? modalIds[0]
      : null;
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
  if (!isSupported(task, env.currentUrl) || env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (!hasTargetContract(task)) return { status: "failed", error_code: "native_save_failed" };
  const contentReady = await waitForNativeSaveReadiness(
    () => !env.isLoggedIn() || env.isUnavailable() || env.isContentReady(),
    env.sleep,
  );
  if (!contentReady) return { status: "failed", error_code: "native_content_not_ready" };
  if (!env.isLoggedIn()) return { status: "login_required" };
  if (env.isUnavailable()) return { status: "unsupported", error_code: "unsupported_content_type" };
  // The modal renders before Douyin hydrates the account-specific collection state.
  // Waiting for the same 15-second SDK/account window used by the page tap prevents
  // a previously-collected white placeholder from being clicked and toggled off.
  await env.waitForAccountState();
  if (!env.isLoggedIn()) return { status: "login_required" };
  if (env.isUnavailable()) return { status: "unsupported", error_code: "unsupported_content_type" };
  if (!env.isContentReady()) return { status: "failed", error_code: "native_content_not_ready" };
  const initial = env.findFavoriteControls(task.content_id);
  if (initial.length !== 1) return { status: "failed", error_code: "native_control_not_found" };
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
      : { status: "failed", error_code: "native_confirmation_not_observed" };
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
    : { status: "failed", error_code: "native_confirmation_not_observed" };
}

export async function verifyDouyin(
  task: NativeSaveTask,
  env: DouyinNativeSaveEnvironment = createDouyinBrowserEnvironment(),
): Promise<unknown> {
  if (!isSupported(task, env.currentUrl) || env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (!hasTargetContract(task)) return { status: "failed", error_code: "native_save_failed" };
  const contentReady = await waitForNativeSaveReadiness(
    () => !env.isLoggedIn() || env.isUnavailable() || env.isContentReady(),
    env.sleep,
  );
  if (!contentReady) return { status: "failed", error_code: "native_content_not_ready" };
  if (!env.isLoggedIn()) return { status: "login_required" };
  if (env.isUnavailable()) return { status: "unsupported", error_code: "unsupported_content_type" };
  await env.waitForAccountState();
  if (!env.isLoggedIn()) return { status: "login_required" };
  if (env.isUnavailable()) return { status: "unsupported", error_code: "unsupported_content_type" };
  if (!env.isContentReady()) return { status: "failed", error_code: "native_content_not_ready" };
  const controls = env.findFavoriteControls(task.content_id);
  if (controls.length !== 1) return { status: "failed", error_code: "native_control_not_found" };
  return controls[0].isSelected()
    ? { status: "already_synced" }
    : { status: "failed", error_code: "native_confirmation_not_observed" };
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
  const explicit = element.getAttribute("aria-pressed") === "true" ||
    element.getAttribute("aria-checked") === "true" ||
    element.getAttribute("data-e2e-state") === "active" ||
    /(?:已收藏|取消收藏)/.test(element.getAttribute("aria-label") ?? element.title ?? element.textContent ?? "");
  if (explicit) return true;
  if (element.getAttribute("data-e2e") !== "video-player-collect") return false;
  const icon = element.querySelectorAll<HTMLElement>("span[role='img']")[0];
  if (!icon) return false;
  const modifiers = (icon.getAttribute("class") ?? "")
    .split(/\s+/)
    .filter((token) => token && token !== "semi-icon" && token !== "semi-icon-default");
  return modifiers.length >= 2;
}

function isExactFavoriteControl(element: HTMLElement): boolean {
  if (["video-favorite", "video-player-collect"].includes(element.getAttribute("data-e2e") ?? "")) {
    return true;
  }
  const label = (
    element.getAttribute("aria-label") ?? element.title ?? element.textContent ?? ""
  ).trim();
  return ["收藏", "已收藏", "取消收藏"].includes(label);
}

function douyinContentContainer(root: Document, contentId: string): HTMLElement | null {
  const activeCandidates = Array.from(root.querySelectorAll<HTMLElement>(
    "[data-e2e='feed-active-video']",
  )).filter((element) => {
    const classTokens = (element.getAttribute("class") ?? "").split(/\s+/);
    return classTokens.includes(`video_${contentId}`) && isEffectivelyVisible(element, root);
  });
  if (activeCandidates.length === 1) return activeCandidates[0];
  if (activeCandidates.length > 1) return null;
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

function routeScopedFavoriteControls(
  root: Document,
  currentUrl: string,
  contentId: string,
): HTMLElement[] {
  if (routeId(currentUrl) !== contentId) return [];
  return Array.from(root.querySelectorAll<HTMLElement>("[data-e2e='video-favorite']"))
    .filter((element) => {
      if (!isEffectivelyVisible(element, root) || !isExactFavoriteControl(element)) return false;
      const owner = element.closest<HTMLElement>(DOUYIN_CONTROL_OWNER_SELECTOR);
      return owner === null;
    });
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
    isContentReady() {
      const contentId = routeId(currentUrl);
      return contentId !== null && (
        douyinContentContainer(root, contentId) !== null ||
        routeScopedFavoriteControls(root, currentUrl, contentId).length > 0
      );
    },
    rateLimitFingerprint() {
      const contentId = routeId(currentUrl);
      if (!contentId) return "";
      const container = douyinContentContainer(root, contentId);
      if (!container) return "";
      return Array.from(container.querySelectorAll<HTMLElement>("[role='alert'], [data-e2e='toast'], .toast"))
        .filter((element) =>
          element.closest(DOUYIN_CONTROL_OWNER_SELECTOR) === container &&
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
      const elements = container
        ? Array.from(container.querySelectorAll<HTMLElement>(
          "button[aria-label*='收藏'], [role='button'][aria-label*='收藏'], [data-e2e='video-favorite'], [data-e2e='video-player-collect']",
        )).filter((element) =>
          element.closest(DOUYIN_CONTROL_OWNER_SELECTOR) === container &&
          isEffectivelyVisible(element, root) && isExactFavoriteControl(element)
        )
        : routeScopedFavoriteControls(root, currentUrl, contentId);
      return elements.map((element) => ({
        isSelected: () => selected(element),
        click: () => element.click(),
      }));
    },
    waitForAccountState() {
      const view = root.defaultView;
      if (!view || typeof view.setTimeout !== "function") return Promise.resolve();
      return new Promise((resolve) => view.setTimeout(resolve, STATE_SETTLE_MS));
    },
    sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    },
  };
}
