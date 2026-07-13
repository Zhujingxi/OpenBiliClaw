import type { NativeSaveTask } from "../../shared/native-save.ts";
import { waitForNativeSaveReadiness } from "./readiness.ts";

export interface YouTubePlaylistRow {
  isChecked(): boolean;
  click(): void;
}

export interface YouTubeNativeSaveEnvironment {
  currentUrl: string;
  isLoggedIn(): boolean;
  isUnavailable(): boolean;
  hasSaveControl(): boolean;
  rateLimitFingerprint(): string;
  openSaveDialog(): Promise<boolean>;
  closeSaveDialog(): Promise<void>;
  findNamedPlaylists(title: string): YouTubePlaylistRow[];
  findWatchLater(): YouTubePlaylistRow | null;
  createPlaylist(title: string): Promise<boolean>;
  sleep(ms: number): Promise<void>;
  dispose?(): void;
}

const EXACT_PLAYLIST_TITLE = "OpenBiliClaw";
const WATCH_LATER_TARGET = "YouTube Watch Later";
const VIDEO_ID_PATTERN = /^[A-Za-z0-9_-]{11}$/;
const CONFIRM_ATTEMPTS = 20;
const CONFIRM_INTERVAL_MS = 100;
const DIALOG_ATTEMPTS = 30;
const DIALOG_INTERVAL_MS = 100;
const RATE_LIMIT_PATTERN = /(?:quota|rate limit|too many requests|try again later|请求过于频繁|操作频繁|配额|稍后再试|リクエストが多すぎ)/i;
const UNAVAILABLE_PATTERN = /(?:video unavailable|this video is private|private video|视频无法播放|视频不可用|私享视频|非公開動画|利用できません)/i;
const SAVE_LABELS = new Set([
  "Save",
  "Save to playlist",
  "保存",
  "保存到播放列表",
  "儲存",
  "再生リストに保存",
]);
const CREATE_LABELS = new Set([
  "Create",
  "Create playlist",
  "创建",
  "创建播放列表",
  "建立",
  "作成",
]);
const NEW_PLAYLIST_LABELS = new Set([
  "New playlist",
  "新建播放列表",
  "建立新播放清單",
  "新しい再生リスト",
]);
const CLOSE_LABELS = new Set(["Close", "Cancel", "关闭", "取消", "關閉", "キャンセル"]);
const CURRENT_URL_QUERY_KEYS = new Set(["v", "feature", "si"]);
const YOUTUBE_HOSTS = new Set(["youtube.com", "www.youtube.com"]);
const RATE_LIMIT_SELECTOR = "tp-yt-paper-toast #text, yt-notification-action-renderer, [role='alert']";
const RATE_LIMIT_CONTAINER_SELECTOR = "tp-yt-paper-toast, yt-notification-action-renderer, [role='alert']";

function secureUrl(value: string): URL | null {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      url.port ||
      url.hash
    ) {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

function taskVideoId(value: string): string | null {
  const url = secureUrl(value);
  if (!url) return null;
  const hostname = url.hostname.toLowerCase();
  if (hostname === "youtu.be") {
    if (url.search !== "") return null;
    return /^\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname)?.[1] ?? null;
  }
  if (!YOUTUBE_HOSTS.has(hostname)) return null;
  if (url.pathname === "/watch") {
    const ids = url.searchParams.getAll("v");
    const id = ids.length === 1 ? ids[0] : "";
    return VIDEO_ID_PATTERN.test(id) && url.search === `?v=${id}` ? id : null;
  }
  if (url.search !== "") return null;
  return /^\/shorts\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname)?.[1] ?? null;
}

function currentVideoId(value: string): string | null {
  const url = secureUrl(value);
  if (!url || !YOUTUBE_HOSTS.has(url.hostname.toLowerCase())) return null;
  let hasUnsupportedQuery = false;
  url.searchParams.forEach((_queryValue, key) => {
    if (!CURRENT_URL_QUERY_KEYS.has(key)) hasUnsupportedQuery = true;
  });
  if (hasUnsupportedQuery) return null;
  if (url.pathname === "/watch") {
    const ids = url.searchParams.getAll("v");
    const id = ids.length === 1 ? ids[0] : "";
    return VIDEO_ID_PATTERN.test(id) ? id : null;
  }
  if (url.searchParams.has("v")) return null;
  return /^\/shorts\/([A-Za-z0-9_-]{11})\/?$/.exec(url.pathname)?.[1] ?? null;
}

function isSupportedTask(task: NativeSaveTask, currentUrl: string): boolean {
  if (task.content_type !== "video" || !VIDEO_ID_PATTERN.test(task.content_id)) return false;
  return taskVideoId(task.content_url) === task.content_id && currentVideoId(currentUrl) === task.content_id;
}

function hasExactActionTarget(task: NativeSaveTask): boolean {
  if (task.requested_action !== task.resolved_action) return false;
  if (task.resolved_action === "favorite") return task.target_label === EXACT_PLAYLIST_TITLE;
  return task.resolved_action === "watch_later" && task.target_label === WATCH_LATER_TARGET;
}

async function confirmed(
  initial: YouTubePlaylistRow,
  lookup: () => YouTubePlaylistRow | null,
  env: YouTubeNativeSaveEnvironment,
  rateLimitBefore: string,
): Promise<boolean> {
  if (initial.isChecked()) return true;
  for (let attempt = 0; attempt < CONFIRM_ATTEMPTS; attempt += 1) {
    const current = lookup();
    if (current?.isChecked()) return true;
    if (hasNewRateLimit(env, rateLimitBefore)) return false;
    if (attempt + 1 < CONFIRM_ATTEMPTS) await env.sleep(CONFIRM_INTERVAL_MS);
  }
  return false;
}

function hasNewRateLimit(env: YouTubeNativeSaveEnvironment, before: string): boolean {
  const after = env.rateLimitFingerprint();
  return after !== "" && after !== before;
}

function uniqueNamedPlaylist(
  env: YouTubeNativeSaveEnvironment,
  title: string,
): YouTubePlaylistRow | null | "ambiguous" {
  const matches = env.findNamedPlaylists(title);
  if (matches.length > 1) return "ambiguous";
  return matches[0] ?? null;
}

async function openDialog(env: YouTubeNativeSaveEnvironment): Promise<boolean> {
  try {
    return await env.openSaveDialog();
  } catch {
    return false;
  }
}

async function performSaveYouTube(task: NativeSaveTask, env: YouTubeNativeSaveEnvironment): Promise<unknown> {
  if (!isSupportedTask(task, env.currentUrl) || env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (!hasExactActionTarget(task)) {
    return { status: "failed", error_code: "native_save_failed" };
  }
  await waitForNativeSaveReadiness(
    () => env.isLoggedIn() || env.isUnavailable(),
    env.sleep,
  );
  if (!env.isLoggedIn()) return { status: "login_required" };
  if (env.isUnavailable()) return { status: "unsupported", error_code: "unsupported_content_type" };
  await waitForNativeSaveReadiness(
    () => env.hasSaveControl() || env.isUnavailable(),
    env.sleep,
  );
  if (env.isUnavailable()) return { status: "unsupported", error_code: "unsupported_content_type" };
  if (!env.hasSaveControl()) return { status: "failed", error_code: "native_save_failed" };
  const rateLimitBefore = env.rateLimitFingerprint();
  if (!(await openDialog(env))) {
    return hasNewRateLimit(env, rateLimitBefore)
      ? { status: "rate_limited" }
      : { status: "failed", error_code: "native_save_failed" };
  }

  if (task.resolved_action === "watch_later") {
    const row = env.findWatchLater();
    if (!row) return { status: "failed", error_code: "native_save_failed" };
    if (row.isChecked()) return { status: "already_synced" };
    try {
      row.click();
    } catch {
      return { status: "failed", error_code: "native_save_failed" };
    }
    if (await confirmed(row, () => env.findWatchLater(), env, rateLimitBefore)) return { status: "synced" };
    return hasNewRateLimit(env, rateLimitBefore)
      ? { status: "rate_limited" }
      : { status: "failed", error_code: "native_save_failed" };
  }

  if (task.resolved_action !== "favorite") {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  let created = false;
  let row = uniqueNamedPlaylist(env, EXACT_PLAYLIST_TITLE);
  if (row === "ambiguous") return { status: "failed", error_code: "native_save_failed" };
  if (!row) {
    try {
      if (!(await env.createPlaylist(EXACT_PLAYLIST_TITLE))) {
        return hasNewRateLimit(env, rateLimitBefore)
          ? { status: "rate_limited" }
          : { status: "failed", error_code: "native_save_failed" };
      }
      created = true;
      await env.closeSaveDialog();
    } catch {
      return { status: "failed", error_code: "native_save_failed" };
    }
    if (!(await openDialog(env))) {
      return hasNewRateLimit(env, rateLimitBefore)
        ? { status: "rate_limited" }
        : { status: "failed", error_code: "native_save_failed" };
    }
    row = uniqueNamedPlaylist(env, EXACT_PLAYLIST_TITLE);
    if (!row || row === "ambiguous") {
      return hasNewRateLimit(env, rateLimitBefore)
        ? { status: "rate_limited" }
        : { status: "failed", error_code: "native_save_failed" };
    }
  }
  if (row.isChecked()) return { status: created ? "synced" : "already_synced" };
  try {
    row.click();
  } catch {
    return { status: "failed", error_code: "native_save_failed" };
  }
  if (
    await confirmed(
      row,
      () => {
        const current = uniqueNamedPlaylist(env, EXACT_PLAYLIST_TITLE);
        return current === "ambiguous" ? null : current;
      },
      env,
      rateLimitBefore,
    )
  ) {
    return { status: "synced" };
  }
  return hasNewRateLimit(env, rateLimitBefore)
    ? { status: "rate_limited" }
    : { status: "failed", error_code: "native_save_failed" };
}

export async function saveYouTube(
  task: NativeSaveTask,
  env: YouTubeNativeSaveEnvironment = createYouTubeBrowserEnvironment(),
): Promise<unknown> {
  try {
    return await performSaveYouTube(task, env);
  } finally {
    try {
      env.dispose?.();
    } catch {
      // Cleanup must not replace the authenticated task result.
    }
  }
}

function isEffectivelyVisible(element: Element, root?: Document): boolean {
  const view = root?.defaultView ?? element.ownerDocument?.defaultView;
  let current: Element | null = element;
  while (current) {
    const html = current as HTMLElement & {
      hasAttribute?: (name: string) => boolean;
      getAttribute?: (name: string) => string | null;
    };
    if (
      html.hidden === true ||
      html.hasAttribute?.("hidden") ||
      html.hasAttribute?.("inert") ||
      html.getAttribute?.("aria-hidden") === "true" ||
      html.style?.display === "none" ||
      html.style?.visibility === "hidden"
    ) {
      return false;
    }
    if (view) {
      const style = view.getComputedStyle(current);
      if (style.display === "none" || style.visibility === "hidden") return false;
    }
    current = html.parentElement;
  }
  return true;
}

function visibleText(element: Element): string {
  return (
    element.getAttribute("aria-label")?.trim() ||
    element.getAttribute("title")?.trim() ||
    element.textContent?.trim() ||
    ""
  );
}

function exactLabeledElement(
  root: ParentNode,
  labels: ReadonlySet<string>,
  documentRoot?: Document,
): HTMLElement | null {
  const matches = Array.from(root.querySelectorAll<HTMLElement>("button, [role='button'], tp-yt-paper-button"))
    .filter((element) => isEffectivelyVisible(element, documentRoot) && labels.has(visibleText(element)));
  return matches.length === 1 ? matches[0] : null;
}

function dialogRoot(root: Document): HTMLElement | null {
  const candidates = Array.from(root.querySelectorAll<HTMLElement>(
    "ytd-add-to-playlist-renderer, tp-yt-paper-dialog ytd-add-to-playlist-renderer",
  ));
  const visible = candidates.filter((element) => isEffectivelyVisible(element, root));
  return visible.length === 1 ? visible[0] : null;
}

function playlistRows(root: ParentNode, documentRoot: Document): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("ytd-playlist-add-to-option-renderer"))
    .filter((row) => isEffectivelyVisible(row, documentRoot));
}

function playlistTitle(row: HTMLElement): string {
  const label = row.querySelector<HTMLElement>("#label, yt-formatted-string#label, .label");
  return label?.textContent?.trim() ?? "";
}

function checked(row: HTMLElement): boolean {
  const checkbox = row.querySelector<HTMLElement>(
    "tp-yt-paper-checkbox, [role='checkbox'], #checkbox",
  );
  if (!checkbox) return false;
  return (
    checkbox.getAttribute("aria-checked") === "true" ||
    checkbox.hasAttribute("checked") ||
    (checkbox as HTMLElement & { checked?: boolean }).checked === true
  );
}

function clickableRow(row: HTMLElement): HTMLElement {
  return row.querySelector<HTMLElement>("tp-yt-paper-checkbox, [role='checkbox'], #checkbox") ?? row;
}

function toPlaylistRow(row: HTMLElement): YouTubePlaylistRow {
  return {
    isChecked: () => checked(row),
    click: () => clickableRow(row).click(),
  };
}

function isWatchLaterRow(row: HTMLElement): boolean {
  const rendererData = (row as HTMLElement & { data?: { playlistId?: unknown } }).data;
  if (
    rendererData?.playlistId === "WL" ||
    row.getAttribute("playlist-id") === "WL" ||
    row.getAttribute("data-playlist-id") === "WL" ||
    row.getAttribute("data-id") === "WL" ||
    Boolean(row.querySelector("[playlist-id='WL'], [data-playlist-id='WL'], [data-id='WL']"))
  ) {
    return true;
  }
  const anchors = (row as HTMLElement & {
    querySelectorAll?: (selector: string) => NodeListOf<HTMLAnchorElement> | HTMLAnchorElement[];
  }).querySelectorAll?.("a[href]") ?? [];
  return Array.from(anchors).some((anchor) => {
    try {
      const link = anchor as HTMLAnchorElement;
      const url = new URL(link.href || link.getAttribute("href") || "", "https://www.youtube.com/");
      return (
        YOUTUBE_HOSTS.has(url.hostname.toLowerCase()) &&
        url.searchParams.getAll("list").length === 1 &&
        url.searchParams.get("list") === "WL"
      );
    } catch {
      return false;
    }
  });
}

function setInputValue(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

export function createYouTubeBrowserEnvironment(
  root: Document = document,
  currentUrl: string = location.href,
): YouTubeNativeSaveEnvironment {
  const rateElementIds = new WeakMap<Element, number>();
  const rateMutationGenerations = new WeakMap<Element, number>();
  const rateEventSnapshots = new WeakMap<Element, { text: string; visible: boolean }>();
  let nextRateElementId = 1;
  const rateEventRoot = (element: Element): Element => element.closest(RATE_LIMIT_CONTAINER_SELECTOR) ?? element;
  const rateEventSnapshot = (element: Element): { text: string; visible: boolean } => ({
    text: element.textContent?.trim() ?? "",
    visible: isEffectivelyVisible(element, root),
  });
  const collectRateEventRoots = (element: Element, found: Set<Element>): void => {
    const direct = element.closest?.(RATE_LIMIT_CONTAINER_SELECTOR);
    if (direct) found.add(direct);
    const descendants = element.querySelectorAll?.(RATE_LIMIT_SELECTOR) ?? [];
    for (const descendant of Array.from(descendants)) found.add(rateEventRoot(descendant));
  };
  const observer = typeof MutationObserver === "undefined" ? null : new MutationObserver((records) => {
    const changedEvents = new Map<Element, boolean>();
    for (const record of records) {
      const target = record.target as Node & {
        closest?: (selector: string) => Element | null;
        parentElement?: Element | null;
        querySelectorAll?: (selector: string) => NodeListOf<Element> | Element[];
      };
      const element = target.nodeType === 3 ? target.parentElement : target;
      if (element) {
        if (record.type === "childList") {
          const event = (element as Element).closest?.(RATE_LIMIT_CONTAINER_SELECTOR);
          if (event) changedEvents.set(event, true);
        } else {
          const found = new Set<Element>();
          collectRateEventRoots(element as Element, found);
          for (const event of found) changedEvents.set(event, changedEvents.get(event) === true);
        }
      }
      if (record.type === "childList") {
        for (const added of Array.from(record.addedNodes ?? [])) {
          const addedElement = added.nodeType === 3 ? added.parentElement : added;
          if (!addedElement) continue;
          const found = new Set<Element>();
          collectRateEventRoots(addedElement as Element, found);
          for (const event of found) changedEvents.set(event, true);
        }
      }
    }
    for (const [event, hasSubtreeAddition] of changedEvents) {
      const previous = rateEventSnapshots.get(event);
      const current = rateEventSnapshot(event);
      if (
        hasSubtreeAddition ||
        (previous !== undefined && (previous.text !== current.text || previous.visible !== current.visible)) ||
        (previous === undefined && current.visible && RATE_LIMIT_PATTERN.test(current.text))
      ) {
        rateMutationGenerations.set(event, (rateMutationGenerations.get(event) ?? 0) + 1);
      }
      rateEventSnapshots.set(event, current);
    }
  });
  try {
    observer?.observe(root, { subtree: true, childList: true, attributes: true, characterData: true });
  } catch {
    observer?.disconnect();
  }
  return {
    currentUrl,
    isLoggedIn() {
      if (root.querySelector("a[href*='ServiceLogin'], ytd-button-renderer a[href*='/signin']")) return false;
      return Boolean(root.querySelector("#avatar-btn, button#avatar-btn, ytd-topbar-menu-button-renderer #avatar"));
    },
    isUnavailable() {
      const elements = root.querySelectorAll<HTMLElement>(
        "ytd-player-error-message-renderer, #error-screen, yt-playability-error-supported-renderers",
      );
      return Array.from(elements).some((element) => UNAVAILABLE_PATTERN.test(element.textContent?.trim() ?? ""));
    },
    hasSaveControl() {
      const stableRoot = root.querySelector<HTMLElement>("ytd-watch-metadata ytd-menu-renderer, #menu ytd-menu-renderer") ?? root;
      return exactLabeledElement(stableRoot, SAVE_LABELS, root) !== null;
    },
    rateLimitFingerprint() {
      const elements = root.querySelectorAll<HTMLElement>(
        RATE_LIMIT_SELECTOR,
      );
      return Array.from(elements)
        .map((element) => {
          const event = rateEventRoot(element);
          const snapshot = rateEventSnapshot(event);
          rateEventSnapshots.set(event, snapshot);
          if (!snapshot.visible || !RATE_LIMIT_PATTERN.test(snapshot.text)) return "";
          let id = rateElementIds.get(event);
          if (id === undefined) {
            id = nextRateElementId;
            nextRateElementId += 1;
            rateElementIds.set(event, id);
          }
          return `${rateMutationGenerations.get(event) ?? 0}:${id}:${snapshot.text}`;
        })
        .filter(Boolean)
        .join("\n");
    },
    async openSaveDialog() {
      const stableRoot = root.querySelector<HTMLElement>("ytd-watch-metadata ytd-menu-renderer, #menu ytd-menu-renderer") ?? root;
      const button = exactLabeledElement(stableRoot, SAVE_LABELS, root);
      if (!button) return false;
      button.click();
      for (let attempt = 0; attempt < DIALOG_ATTEMPTS; attempt += 1) {
        if (dialogRoot(root)) return true;
        if (attempt + 1 < DIALOG_ATTEMPTS) await this.sleep(DIALOG_INTERVAL_MS);
      }
      return false;
    },
    async closeSaveDialog() {
      const dialog = dialogRoot(root);
      if (!dialog) return;
      const close = dialog.querySelector<HTMLElement>("#close-button, yt-icon-button#close-button")
        ?? exactLabeledElement(dialog, CLOSE_LABELS, root);
      close?.click();
      await this.sleep(DIALOG_INTERVAL_MS);
    },
    findNamedPlaylists(title) {
      const dialog = dialogRoot(root);
      if (!dialog) return [];
      return playlistRows(dialog, root).filter((row) => playlistTitle(row) === title).map(toPlaylistRow);
    },
    findWatchLater() {
      const dialog = dialogRoot(root);
      if (!dialog) return null;
      const rows = playlistRows(dialog, root).filter(isWatchLaterRow);
      return rows.length === 1 ? toPlaylistRow(rows[0]) : null;
    },
    async createPlaylist(title) {
      const dialog = dialogRoot(root);
      if (!dialog) return false;
      const newPlaylist = dialog.querySelector<HTMLElement>("#new-playlist-button")
        ?? exactLabeledElement(dialog, NEW_PLAYLIST_LABELS, root);
      if (!newPlaylist) return false;
      newPlaylist.click();
      for (let attempt = 0; attempt < DIALOG_ATTEMPTS; attempt += 1) {
        const input = dialog.querySelector<HTMLInputElement>(
          "input#input, tp-yt-paper-input input, input[aria-label*='name' i]",
        );
        if (input) {
          setInputValue(input, title);
          const create = exactLabeledElement(dialog, CREATE_LABELS, root);
          if (!create) return false;
          create.click();
          await this.sleep(DIALOG_INTERVAL_MS);
          return true;
        }
        if (attempt + 1 < DIALOG_ATTEMPTS) await this.sleep(DIALOG_INTERVAL_MS);
      }
      return false;
    },
    sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    },
    dispose() {
      observer?.disconnect();
    },
  };
}
