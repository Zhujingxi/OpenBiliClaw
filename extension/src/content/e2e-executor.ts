import {
  E2E_STATE_CHANGING_ACTIONS,
  type E2EAction,
  type E2EActionExecutionResult,
  type E2EContentExecuteMessage,
  type E2EPlatform,
} from "../shared/e2e.ts";

type E2EContentExecutionResponse = {
  status: "ok" | "failed";
  actions: E2EActionExecutionResult[];
  error?: string;
};

type ElementRect = Pick<DOMRect, "width" | "height" | "top" | "left" | "bottom" | "right">;

interface ClickableElement {
  textContent?: string | null;
  click?: () => void;
  dispatchEvent?: (event: Event) => boolean;
  getAttribute?: (name: string) => string | null;
  getBoundingClientRect?: () => ElementRect;
  scrollIntoView?: (options?: ScrollIntoViewOptions) => void;
}

interface QueryDocument {
  querySelectorAll: (selector: string) => Iterable<ClickableElement> | ArrayLike<ClickableElement>;
}

interface ScrollWindow {
  innerHeight: number;
  scrollBy: (options: ScrollToOptions) => void;
}

export interface E2EExecutorEnv {
  document?: QueryDocument;
  window?: ScrollWindow;
  sleep?: (ms: number) => Promise<void>;
}

interface PlatformRecipe {
  clickSelectors: readonly string[];
  textActions: Partial<Record<E2EAction, readonly RegExp[]>>;
}

const SNAPSHOT_WAIT_MS = 150;
const SCROLL_WAIT_MS = 300;
const CLICK_WAIT_MS = 200;
const TEXT_TARGET_SELECTOR = 'button, [role="button"], a, div, span';

const KNOWN_ACTIONS = new Set<E2EAction>([
  "snapshot",
  "scroll",
  "click",
  "like",
  "favorite",
  "share",
  "follow",
  "repost",
  "bookmark",
]);

const RECIPES: Record<E2EPlatform, PlatformRecipe> = {
  douyin: {
    clickSelectors: ['[data-e2e="feed-active-video"]', "video", '[role="button"]'],
    textActions: {
      share: [/share/i, /分享/],
      like: [/like/i, /赞/],
      favorite: [/favorite/i, /收藏/],
      follow: [/follow/i, /关注/],
    },
  },
  xiaohongshu: {
    clickSelectors: [".note-item", 'a[href*="/explore/"]', "section"],
    textActions: {
      share: [/share/i, /分享/],
      like: [/like/i, /赞/],
      favorite: [/collect/i, /收藏/],
      follow: [/follow/i, /关注/],
    },
  },
  twitter: {
    clickSelectors: ['article [role="link"]', "article", '[data-testid="tweet"]'],
    textActions: {
      share: [/share/i],
      repost: [/repost/i, /retweet/i],
      like: [/like/i],
      favorite: [/bookmark/i],
      bookmark: [/bookmark/i],
      follow: [/follow/i],
    },
  },
};

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function getEnv(env?: E2EExecutorEnv): Required<E2EExecutorEnv> {
  const fallbackDocument: QueryDocument = {
    querySelectorAll: () => [],
  };
  const fallbackWindow: ScrollWindow = {
    innerHeight: 0,
    scrollBy: () => {
      throw new Error("window unavailable");
    },
  };
  return {
    document: env?.document ?? (typeof document === "undefined" ? fallbackDocument : document),
    window: env?.window ?? (typeof window === "undefined" ? fallbackWindow : window),
    sleep: env?.sleep ?? defaultSleep,
  };
}

function isVisible(element: ClickableElement): boolean {
  const rect = element.getBoundingClientRect?.();
  if (!rect) return false;
  return rect.width > 0 && rect.height > 0;
}

function queryVisible(documentLike: QueryDocument, selectors: readonly string[]): ClickableElement | null {
  for (const selector of selectors) {
    const elements = Array.from(documentLike.querySelectorAll(selector));
    const target = elements.find(isVisible);
    if (target) return target;
  }
  return null;
}

function elementLabel(element: ClickableElement): string {
  return [
    element.getAttribute?.("aria-label"),
    element.getAttribute?.("title"),
    element.textContent,
  ]
    .filter((value): value is string => typeof value === "string")
    .join(" ")
    .trim();
}

function findTextTarget(
  documentLike: QueryDocument,
  patterns: readonly RegExp[],
): ClickableElement | null {
  const elements = Array.from(documentLike.querySelectorAll(TEXT_TARGET_SELECTOR));
  return elements.find((element) => {
    if (!isVisible(element)) return false;
    const label = elementLabel(element);
    return patterns.some((pattern) => pattern.test(label));
  }) ?? null;
}

function clickElement(element: ClickableElement): void {
  element.scrollIntoView?.({ block: "center", inline: "center", behavior: "smooth" });
  if (typeof element.click === "function") {
    element.click();
    return;
  }
  element.dispatchEvent?.(
    new MouseEvent("click", {
      bubbles: true,
      cancelable: true,
      view: window,
    }),
  );
}

function failedResult(action: E2EAction, detail: string, error?: string): E2EActionExecutionResult {
  return {
    action,
    status: "failed",
    detail,
    ...(error ? { error } : {}),
  };
}

function isExecuteMessage(value: unknown, platform: E2EPlatform): value is E2EContentExecuteMessage {
  if (!value || typeof value !== "object") return false;
  const message = value as Partial<E2EContentExecuteMessage>;
  return (
    message.action === "OBC_E2E_EXECUTE" &&
    message.platform === platform &&
    typeof message.runId === "string" &&
    message.runId.length > 0 &&
    Array.isArray(message.actions) &&
    message.actions.every((action) => KNOWN_ACTIONS.has(action)) &&
    typeof message.allowStateChanging === "boolean"
  );
}

export async function executeAction(
  platform: E2EPlatform,
  action: E2EAction,
  allowStateChanging: boolean,
  env?: E2EExecutorEnv,
): Promise<E2EActionExecutionResult> {
  try {
    const runtime = getEnv(env);
    if (action === "snapshot") {
      await runtime.sleep(SNAPSHOT_WAIT_MS);
      return { action, status: "ok", detail: "snapshot", executed: true };
    }

    if (action === "scroll") {
      runtime.window.scrollBy({
        top: Math.max(300, runtime.window.innerHeight * 0.75),
        behavior: "smooth",
      });
      await runtime.sleep(SCROLL_WAIT_MS);
      return { action, status: "ok", detail: "scrolled" };
    }

    if (E2E_STATE_CHANGING_ACTIONS.has(action) && !allowStateChanging) {
      return {
        action,
        status: "skipped",
        detail: "state_changing_action_blocked",
      };
    }

    const recipe = RECIPES[platform];
    const target = action === "click"
      ? queryVisible(runtime.document, recipe.clickSelectors)
      : findTextTarget(runtime.document, recipe.textActions[action] ?? []);

    if (!target) {
      return failedResult(action, "target_not_found");
    }

    clickElement(target);
    await runtime.sleep(CLICK_WAIT_MS);
    return { action, status: "ok", detail: "clicked" };
  } catch (error) {
    return failedResult(
      action,
      "execution_error",
      error instanceof Error ? error.message : String(error),
    );
  }
}

async function executeActions(message: E2EContentExecuteMessage): Promise<E2EContentExecutionResponse> {
  const actions: E2EActionExecutionResult[] = [];
  for (const action of message.actions) {
    actions.push(await executeAction(message.platform, action, message.allowStateChanging));
  }
  const failed = actions.find((result) => result.status === "failed");
  return failed
    ? { status: "failed", actions, error: failed.error ?? failed.detail }
    : { status: "ok", actions };
}

export function registerE2EExecutor(platform: E2EPlatform): void {
  if (typeof chrome === "undefined" || !chrome.runtime?.onMessage) return;

  chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
    if (!isExecuteMessage(message, platform)) return false;

    void executeActions(message)
      .then(sendResponse)
      .catch((error: unknown) => {
        sendResponse({
          status: "failed",
          actions: [],
          error: error instanceof Error ? error.message : String(error),
        });
      });

    return true;
  });
}
