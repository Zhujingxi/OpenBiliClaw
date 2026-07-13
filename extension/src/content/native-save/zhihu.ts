import type { NativeSaveTask } from "../../shared/native-save.ts";
import { waitForNativeSaveReadiness } from "./readiness.ts";

export interface ZhihuCollectionRow {
  isChecked(): boolean;
  click(): void;
}

export interface ZhihuNativeSaveEnvironment {
  currentUrl: string;
  hasVisibleLoginOverlay(): boolean;
  isUnavailable(): boolean;
  hasCollectionControl(): boolean;
  rateLimitFingerprint(): string;
  openCollectionDialog(): Promise<boolean>;
  closeCollectionDialog(): Promise<void>;
  findNamedCollections(title: string): ZhihuCollectionRow[];
  createCollection(title: string): Promise<boolean>;
  creationFailureCode?(): ZhihuCreationFailureCode;
  sleep(ms: number): Promise<void>;
}

type ZhihuCreationFailureCode =
  | "native_control_not_found"
  | "native_dialog_not_opened"
  | "native_target_not_found"
  | "native_request_rejected"
  | "native_confirmation_not_observed";

const EXACT_COLLECTION_TITLE = "OpenBiliClaw";
const CREATE_COLLECTION_LABELS = new Set([
  "新建收藏夹",
  "创建收藏夹",
  "添加收藏夹",
  "新增收藏夹",
  "新建收藏",
  "新建",
  "Create collection",
  "New collection",
]);
const CONFIRM_COLLECTION_LABELS = new Set([
  "创建",
  "确认",
  "完成",
  "确定",
  "确认创建",
  "创建收藏夹",
]);
const TYPED_ID = /^(question|answer|article):([0-9]+)$/;
const CONFIRM_ATTEMPTS = 20;
const CONFIRM_INTERVAL_MS = 100;
const DIALOG_ATTEMPTS = 20;
const DIALOG_INTERVAL_MS = 100;
const IDENTITY_SELECTOR = [
  "[data-question-id]", "[data-za-question-id]", "[data-zop-questionid]",
  "[data-answer-id]", "[data-za-answer-id]", "[data-zop-answerid]",
  "[data-article-id]", "[data-zop-articleid]", "[data-zop]",
].join(", ");
const DIALOG_SELECTOR = "[role='dialog'], .Modal-wrapper, .FavlistDialog, .CollectionDialog";
const RATE_SELECTOR = "[role='alert'], .Toast, .Modal-toast, .Notification";
const RATE_PATTERN = /(?:操作频繁|请求频繁|稍后再试|风险|风控|rate limit|too many requests|risk control|429)/i;

function pageIdentity(value: string): string | null {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" || url.username || url.password || url.port ||
      url.hash || url.search
    ) return null;
    const host = url.hostname.toLowerCase();
    if (host === "www.zhihu.com" || host === "zhihu.com") {
      const answer = /^\/question\/[0-9]+\/answer\/([0-9]+)\/?$/.exec(url.pathname);
      if (answer) return `answer:${answer[1]}`;
      const question = /^\/question\/([0-9]+)\/?$/.exec(url.pathname);
      if (question) return `question:${question[1]}`;
      const article = /^\/p\/([0-9]+)\/?$/.exec(url.pathname);
      if (article) return `article:${article[1]}`;
    }
    if (host === "zhuanlan.zhihu.com") {
      const article = /^\/p\/([0-9]+)\/?$/.exec(url.pathname);
      if (article) return `article:${article[1]}`;
    }
    return null;
  } catch {
    return null;
  }
}

function pageRouteKey(value: string): string | null {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" || url.username || url.password || url.port ||
      url.hash || url.search
    ) return null;
    const host = url.hostname.toLowerCase();
    if (host === "www.zhihu.com" || host === "zhihu.com") {
      const answer = /^\/question\/([0-9]+)\/answer\/([0-9]+)\/?$/.exec(url.pathname);
      if (answer) return `answer:${answer[2]}@question:${answer[1]}`;
    }
    const identity = pageIdentity(value);
    return identity ? `${identity}@${host}` : null;
  } catch {
    return null;
  }
}

function isEffectivelyVisible(element: HTMLElement, root: Document): boolean {
  const view = root.defaultView ?? element.ownerDocument?.defaultView;
  let current: HTMLElement | null = element;
  while (current) {
    if (
      current.hidden || current.hasAttribute?.("hidden") || current.hasAttribute?.("inert") ||
      current.getAttribute?.("aria-hidden") === "true" || current.style?.display === "none" ||
      current.style?.visibility === "hidden"
    ) return false;
    if (view) {
      const style = view.getComputedStyle(current);
      if (style.display === "none" || style.visibility === "hidden") return false;
    }
    current = current.parentElement;
  }
  return true;
}

function identityFromElement(element: HTMLElement): string | null {
  const direct: ReadonlyArray<[string, string]> = [
    ["question", "data-question-id"], ["question", "data-za-question-id"],
    ["question", "data-zop-questionid"], ["answer", "data-answer-id"],
    ["answer", "data-za-answer-id"], ["answer", "data-zop-answerid"],
    ["article", "data-article-id"], ["article", "data-zop-articleid"],
  ];
  for (const [kind, attribute] of direct) {
    const id = element.getAttribute?.(attribute) ?? "";
    if (/^[0-9]+$/.test(id)) return `${kind}:${id}`;
  }
  const raw = element.getAttribute?.("data-zop");
  if (!raw) return null;
  try {
    const data = JSON.parse(raw) as Record<string, unknown>;
    const kind = String(data.type ?? data.itemType ?? "").toLowerCase();
    const id = String(data.itemId ?? data.id ?? "");
    return /^(?:question|answer|article)$/.test(kind) && /^[0-9]+$/.test(id)
      ? `${kind}:${id}`
      : null;
  } catch {
    return null;
  }
}

function closestIdentity(element: HTMLElement): string | null {
  let current: HTMLElement | null = element;
  while (current) {
    const identity = identityFromElement(current);
    if (identity) return identity;
    current = current.parentElement;
  }
  return null;
}

function closestDialog(element: HTMLElement): HTMLElement | null {
  let current: HTMLElement | null = element;
  while (current) {
    if (
      current.getAttribute?.("role") === "dialog" ||
      /(?:^|\s)(?:Modal-wrapper|FavlistDialog|CollectionDialog)(?:\s|$)/.test(
        current.getAttribute?.("class") ?? "",
      )
    ) return current;
    current = current.parentElement;
  }
  return null;
}

function visibleText(element: HTMLElement): string {
  return (
    element.getAttribute?.("aria-label") || element.getAttribute?.("title") ||
    element.textContent || ""
  ).trim();
}

function actionLabel(element: HTMLElement): string {
  return visibleText(element).replace(/^[+＋]\s*/, "");
}

function isSupported(task: NativeSaveTask, currentUrl: string): boolean {
  const match = TYPED_ID.exec(task.content_id);
  if (!match) return false;
  const kind = match[1];
  return task.platform === "zhihu" && task.platform_slug === "zhihu" &&
    task.item_key === `zhihu:${task.content_id}` && task.content_type === kind &&
    pageIdentity(task.content_url) === task.content_id &&
    pageIdentity(currentUrl) === task.content_id &&
    pageRouteKey(task.content_url) === pageRouteKey(currentUrl);
}

function hasTargetContract(task: NativeSaveTask): boolean {
  return task.target_label === EXACT_COLLECTION_TITLE && task.resolved_action === "favorite" &&
    (task.requested_action === "favorite" || task.requested_action === "watch_later");
}

function hasNewRateLimit(before: string, after: string): boolean {
  const baseline = new Set(before.split("\n").filter(Boolean));
  return after.split("\n").filter(Boolean).some((event) => !baseline.has(event));
}

async function confirmChecked(env: ZhihuNativeSaveEnvironment): Promise<boolean> {
  for (let attempt = 0; attempt < CONFIRM_ATTEMPTS; attempt += 1) {
    const rows = env.findNamedCollections(EXACT_COLLECTION_TITLE);
    if (rows.length === 1 && rows[0].isChecked()) return true;
    if (attempt + 1 < CONFIRM_ATTEMPTS) await env.sleep(CONFIRM_INTERVAL_MS);
  }
  return false;
}

async function waitForExactCollection(
  env: ZhihuNativeSaveEnvironment,
): Promise<ZhihuCollectionRow | null | "ambiguous"> {
  for (let attempt = 0; attempt < CONFIRM_ATTEMPTS; attempt += 1) {
    const rows = env.findNamedCollections(EXACT_COLLECTION_TITLE);
    if (rows.length > 1) return "ambiguous";
    if (rows.length === 1) return rows[0];
    if (attempt + 1 < CONFIRM_ATTEMPTS) await env.sleep(CONFIRM_INTERVAL_MS);
  }
  return null;
}

/** Save one exact typed Zhihu item to the exact OpenBiliClaw collection. */
export async function saveZhihu(
  task: NativeSaveTask,
  env: ZhihuNativeSaveEnvironment = createZhihuBrowserEnvironment(),
): Promise<unknown> {
  if (!isSupported(task, env.currentUrl) || env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (!hasTargetContract(task)) return { status: "failed", error_code: "native_save_failed" };
  const rateBefore = env.rateLimitFingerprint();
  await waitForNativeSaveReadiness(
    () => env.hasVisibleLoginOverlay() || env.isUnavailable() || env.hasCollectionControl(),
    env.sleep,
  );
  if (env.hasVisibleLoginOverlay()) return { status: "login_required" };
  if (env.isUnavailable()) {
    return { status: "unsupported", error_code: "unsupported_content_type" };
  }
  if (!env.hasCollectionControl()) {
    return hasNewRateLimit(rateBefore, env.rateLimitFingerprint())
      ? { status: "rate_limited" }
      : { status: "failed", error_code: "native_control_not_found" };
  }
  const dialogOpened = await env.openCollectionDialog();
  if (!dialogOpened) {
    return hasNewRateLimit(rateBefore, env.rateLimitFingerprint())
      ? { status: "rate_limited" }
      : { status: "failed", error_code: "native_dialog_not_opened" };
  }
  let created = false;
  let rows = env.findNamedCollections(EXACT_COLLECTION_TITLE);
  if (rows.length > 1) return { status: "failed", error_code: "native_target_not_found" };
  if (rows.length === 0) {
    if (!(await env.createCollection(EXACT_COLLECTION_TITLE))) {
      return hasNewRateLimit(rateBefore, env.rateLimitFingerprint())
        ? { status: "rate_limited" }
        : {
            status: "failed",
            error_code: env.creationFailureCode?.() ?? "native_request_rejected",
          };
    }
    created = true;
    await env.closeCollectionDialog();
    if (!(await env.openCollectionDialog())) {
      return { status: "failed", error_code: "native_dialog_not_opened" };
    }
    const createdRow = await waitForExactCollection(env);
    if (!createdRow || createdRow === "ambiguous") {
      return hasNewRateLimit(rateBefore, env.rateLimitFingerprint())
        ? { status: "rate_limited" }
        : { status: "failed", error_code: "native_target_not_found" };
    }
    rows = [createdRow];
  }
  const row = rows[0];
  if (row.isChecked()) return { status: created ? "synced" : "already_synced" };
  try {
    row.click();
  } catch {
    return { status: "failed", error_code: "native_request_rejected" };
  }
  if (await confirmChecked(env)) return { status: "synced" };
  return hasNewRateLimit(rateBefore, env.rateLimitFingerprint())
    ? { status: "rate_limited" }
    : { status: "failed", error_code: "native_confirmation_not_observed" };
}

export function createZhihuBrowserEnvironment(
  root: Document = document,
  currentUrl: string = location.href,
): ZhihuNativeSaveEnvironment {
  const currentIdentity = pageIdentity(currentUrl);
  const rateElementIds = new WeakMap<Element, number>();
  let nextRateElementId = 1;
  let activeContainer: HTMLElement | null = null;
  let activeDialog: HTMLElement | null = null;
  let lastCreationFailure: ZhihuCreationFailureCode = "native_request_rejected";

  const failCreation = (code: ZhihuCreationFailureCode): false => {
    lastCreationFailure = code;
    return false;
  };

  const targetContainer = (): HTMLElement | null => {
    if (!currentIdentity) return null;
    const matches = Array.from(root.querySelectorAll<HTMLElement>(IDENTITY_SELECTOR)).filter(
      (element) => identityFromElement(element) === currentIdentity &&
        isEffectivelyVisible(element, root),
    );
    if (matches.length !== 1) return null;
    return matches[0];
  };

  const visibleDialogs = (): HTMLElement[] => Array.from(
    root.querySelectorAll<HTMLElement>(DIALOG_SELECTOR),
  ).filter((dialog) => isEffectivelyVisible(dialog, root));

  const dialogMatchesIdentity = (dialog: HTMLElement): boolean => {
    const identity = closestIdentity(dialog);
    return identity === null || identity === currentIdentity;
  };

  const findCollectionRows = (title: string): HTMLElement[] => {
    if (!activeDialog || !isEffectivelyVisible(activeDialog, root)) return [];
    return Array.from(activeDialog.querySelectorAll<HTMLElement>(
      "[data-collection-id], [data-favlist-id], [data-testid='collection-row'], .CollectionList-item, .FavlistItem",
    )).filter((row) => {
      const identity = closestIdentity(row);
      if (
        !isEffectivelyVisible(row, root) || closestDialog(row) !== activeDialog ||
        (identity !== null && identity !== currentIdentity)
      ) return false;
      const titleNode = row.querySelector<HTMLElement>(
        "[data-collection-title], .Collection-title, .Favlist-title, .Modal-listItem-title",
      );
      return (titleNode?.textContent ?? row.textContent ?? "") === title;
    });
  };

  const rowChecked = (row: HTMLElement): boolean => {
    const checkbox = row.querySelector<HTMLElement>(
      "input[type='checkbox'], [role='checkbox'], [aria-checked]",
    );
    const state = checkbox ?? row;
    return state.getAttribute?.("aria-checked") === "true" ||
      state.getAttribute?.("data-checked") === "true" || state.hasAttribute?.("checked") ||
      (state as HTMLElement & { checked?: boolean }).checked === true;
  };

  const clickRow = (row: HTMLElement): void => {
    const checkbox = row.querySelector<HTMLElement>(
      "input[type='checkbox'], [role='checkbox'], [aria-checked]",
    );
    (checkbox ?? row).click();
  };

  const collectionControls = (): HTMLElement[] => {
    const container = targetContainer();
    if (!container || !currentIdentity) return [];
    return Array.from(container.querySelectorAll<HTMLElement>(
      "button, [role='button']",
    )).filter((element) => isEffectivelyVisible(element, root) &&
      closestIdentity(element) === currentIdentity &&
      ["收藏", "已收藏", "取消收藏"].includes(visibleText(element)));
  };

  return {
    currentUrl,
    hasVisibleLoginOverlay() {
      return Array.from(root.querySelectorAll<HTMLElement>(
        "[data-testid='login-modal'], .SignFlow, .Modal-wrapper .Login-content, [role='dialog'] form[action*='signin']",
      )).some((element) => isEffectivelyVisible(element, root));
    },
    isUnavailable() {
      return Array.from(root.querySelectorAll<HTMLElement>(
        ".ErrorPage, .NotFound, [data-testid='content-unavailable']",
      )).some((element) => isEffectivelyVisible(element, root) &&
        /(?:不存在|已删除|内容不可用|页面不存在|not found|deleted|unavailable)/i.test(
          element.textContent ?? "",
        ));
    },
    hasCollectionControl() {
      return collectionControls().length === 1;
    },
    rateLimitFingerprint() {
      const container = activeContainer ?? targetContainer();
      const roots = [container, activeDialog].filter((value): value is HTMLElement => value !== null);
      const events = new Set<HTMLElement>();
      for (const scope of roots) {
        for (const element of Array.from(scope.querySelectorAll<HTMLElement>(RATE_SELECTOR))) {
          const inTargetIdentity = closestIdentity(element);
          if (inTargetIdentity !== null && inTargetIdentity !== currentIdentity) continue;
          const dialog = closestDialog(element);
          if (dialog !== null && dialog !== activeDialog) continue;
          if (!isEffectivelyVisible(element, root) || !RATE_PATTERN.test(element.textContent ?? "")) continue;
          events.add(element);
        }
      }
      return [...events].map((element) => {
        let id = rateElementIds.get(element);
        if (id === undefined) {
          id = nextRateElementId;
          nextRateElementId += 1;
          rateElementIds.set(element, id);
        }
        return `${id}:${(element.textContent ?? "").trim()}`;
      }).sort().join("\n");
    },
    async openCollectionDialog() {
      const container = targetContainer();
      if (!container || !currentIdentity) return false;
      const controls = collectionControls();
      if (controls.length !== 1) return false;
      const before = new Set(visibleDialogs());
      activeContainer = container;
      controls[0].click();
      for (let attempt = 0; attempt < DIALOG_ATTEMPTS; attempt += 1) {
        const candidates = visibleDialogs().filter((dialog) =>
          !before.has(dialog) && dialogMatchesIdentity(dialog));
        if (candidates.length === 1) {
          activeDialog = candidates[0];
          return true;
        }
        if (candidates.length > 1) return false;
        if (attempt + 1 < DIALOG_ATTEMPTS) {
          await new Promise((resolve) => setTimeout(resolve, DIALOG_INTERVAL_MS));
        }
      }
      return false;
    },
    async closeCollectionDialog() {
      const dialog = activeDialog;
      if (!dialog) return;
      const closeControls = Array.from(dialog.querySelectorAll<HTMLElement>(
        "button, [role='button']",
      )).filter((element) => isEffectivelyVisible(element, root) &&
        closestDialog(element) === dialog &&
        (closestIdentity(element) === null || closestIdentity(element) === currentIdentity) &&
        ["关闭", "取消", "Close", "Cancel"].includes(visibleText(element)));
      if (closeControls.length === 1) {
        closeControls[0].click();
        await new Promise((resolve) => setTimeout(resolve, DIALOG_INTERVAL_MS));
      }
      activeDialog = null;
    },
    findNamedCollections(title) {
      return findCollectionRows(title).map((row) => ({
        isChecked: () => rowChecked(row),
        click: () => clickRow(row),
      }));
    },
    async createCollection(title) {
      const dialog = activeDialog;
      if (!dialog) return failCreation("native_dialog_not_opened");
      lastCreationFailure = "native_request_rejected";
      const createControls = Array.from(dialog.querySelectorAll<HTMLElement>(
        "button, [role='button']",
      )).filter((element) => isEffectivelyVisible(element, root) && closestDialog(element) === dialog &&
        (closestIdentity(element) === null || closestIdentity(element) === currentIdentity) &&
        CREATE_COLLECTION_LABELS.has(actionLabel(element)));
      if (createControls.length !== 1) return failCreation("native_control_not_found");
      const dialogsBeforeCreate = new Set(visibleDialogs());
      createControls[0].click();

      let input: HTMLInputElement | null = null;
      let formDialog: HTMLElement | null = null;
      for (let attempt = 0; attempt < DIALOG_ATTEMPTS; attempt += 1) {
        const scopes = [
          dialog,
          ...visibleDialogs().filter((candidate) =>
            !dialogsBeforeCreate.has(candidate) && dialogMatchesIdentity(candidate)),
        ];
        const matches = scopes.flatMap((scope) =>
          Array.from(scope.querySelectorAll<HTMLInputElement>(
            "input[name='title'], input[placeholder*='收藏夹'], input[placeholder*='名称']",
          )).filter((candidate) => isEffectivelyVisible(candidate, root) &&
            closestDialog(candidate) === scope &&
            (closestIdentity(candidate) === null || closestIdentity(candidate) === currentIdentity))
            .map((candidate) => ({ input: candidate, scope })),
        );
        if (matches.length > 1) return failCreation("native_target_not_found");
        if (matches.length === 1) {
          input = matches[0].input;
          formDialog = matches[0].scope;
          break;
        }
        if (attempt + 1 < DIALOG_ATTEMPTS) {
          await new Promise((resolve) => setTimeout(resolve, DIALOG_INTERVAL_MS));
        }
      }
      if (!input || !formDialog) return failCreation("native_dialog_not_opened");
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      if (setter) setter.call(input, title);
      else input.value = title;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      const confirms = Array.from(formDialog.querySelectorAll<HTMLElement>(
        "button, [role='button']",
      )).filter((element) => isEffectivelyVisible(element, root) && closestDialog(element) === formDialog &&
        (closestIdentity(element) === null || closestIdentity(element) === currentIdentity) &&
        CONFIRM_COLLECTION_LABELS.has(actionLabel(element)));
      if (confirms.length !== 1) return failCreation("native_request_rejected");
      confirms[0].click();

      for (let attempt = 0; attempt < DIALOG_ATTEMPTS; attempt += 1) {
        const exactRows = findCollectionRows(title);
        if (exactRows.length > 1) return failCreation("native_target_not_found");
        if (exactRows.length === 1) return true;
        const remainingInputs = Array.from(formDialog.querySelectorAll<HTMLInputElement>(
          "input[name='title'], input[placeholder*='收藏夹'], input[placeholder*='名称']",
        )).filter((candidate) => isEffectivelyVisible(candidate, root) &&
          closestDialog(candidate) === formDialog &&
          (closestIdentity(candidate) === null || closestIdentity(candidate) === currentIdentity));
        if (remainingInputs.length === 0) return true;
        if (remainingInputs.length > 1) return failCreation("native_target_not_found");
        if (attempt + 1 < DIALOG_ATTEMPTS) {
          await new Promise((resolve) => setTimeout(resolve, DIALOG_INTERVAL_MS));
        }
      }
      return failCreation("native_confirmation_not_observed");
    },
    creationFailureCode() {
      return lastCreationFailure;
    },
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  };
}
