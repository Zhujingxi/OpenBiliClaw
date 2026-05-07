/**
 * OpenBiliClaw — Douyin content script entry.
 *
 * Injected into douyin.com pages (isolated world). Listens for
 * DY_SCOPE_EXECUTE messages from the dispatcher, drives the per-scope
 * scrape — installs the MAIN-world fetch-tap (if not already), waits
 * for it to capture aweme JSON, programmatically scrolls to trigger
 * Douyin's virtual-list pagination, accumulates items into a
 * BootstrapItemSink, and posts DY_SCOPE_RESULT back when the scope is
 * exhausted (cap hit / round budget gone / consecutive stagnant
 * rounds).
 *
 * The MAIN-world fetch-tap is loaded separately via the
 * content_scripts MAIN-world entry in manifest.json
 * (dist/main/dy-fetch-tap.js, runs at document_start). That script
 * postMessages captured items here using a sentinel type
 * OPENBILICLAW_DOUYIN_AWEME_PAGE.
 *
 * Module isolation: zero imports from extension/src/content/xhs/.
 */

import type { DouyinBootstrapItem, DouyinScope } from "../main/dy-fetch-tap.js";

// Dynamic import for the chrome-lifecycle code path so node:test's
// --experimental-strip-types resolver doesn't have to chase the
// `.js → .ts` chain at module-load time. Pure helpers exported from
// this file (isValidScopeExecuteMessage) stay synchronously
// importable for unit tests. esbuild inlines the dynamic import at
// build time, so production runtime sees no extra latency.
async function loadTaskExecutorHelpers(): Promise<{
  BootstrapItemSink: typeof import("./dy/task-executor.js").BootstrapItemSink;
  dyShouldContinueScroll: typeof import("./dy/task-executor.js").dyShouldContinueScroll;
  ingestMainWorldFetchMessage: typeof import("./dy/task-executor.js").ingestMainWorldFetchMessage;
}> {
  return await import("./dy/task-executor.js");
}

interface ScopeExecuteMessage {
  task_id: string;
  scope: DouyinScope;
  max_items_per_scope: number;
  max_scroll_rounds: number;
  max_stagnant_scroll_rounds: number;
  debug_inject_status?: string;
}

interface ScopeResultPayload {
  task_id: string;
  scope: DouyinScope;
  items: DouyinBootstrapItem[];
  scope_count: number;
  status: "ok" | "empty" | "failed";
  error?: string;
  /**
   * Diagnostic counters surfaced through the dispatcher into the
   * /api/sources/dy/task-result partial debug field. Lets us
   * disambiguate "scope returned empty because fetch-tap never
   * installed" from "fetch-tap installed but Douyin returned empty
   * 200s (risk control)" without needing the user's browser console.
   */
  debug?: {
    fetch_tap_install_status: "unknown" | "installed" | "skipped_no_sdk";
    aweme_messages_received: number;
    install_messages_received: number;
    inject_status?: string;
    page_url?: string;
    profile_link_found?: boolean;
    sub_tab_found?: boolean;
  };
}

const SCROLL_DELAY_MS = 1_500;
const POST_INSTALL_SETTLE_MS = 800;

// Module-level: track the last fetch-tap install ping. The MAIN-world
// dy-fetch-tap.js posts one of:
//   { type: "OPENBILICLAW_DOUYIN_FETCH_TAP_INSTALL", status: "installed" }
//   { type: "OPENBILICLAW_DOUYIN_FETCH_TAP_INSTALL", status: "skipped_no_sdk" }
// at install resolve. We capture it here so runScope can include the
// status in the result payload's debug field — that's how dispatcher
// diagnostic logs see whether the MAIN-world script actually wrapped
// fetch in this tab.
let _lastFetchTapInstallStatus: "unknown" | "installed" | "skipped_no_sdk" = "unknown";
let _installMessagesReceived = 0;
if (typeof window !== "undefined") {
  window.addEventListener("message", (event: MessageEvent) => {
    const data = event?.data as { type?: unknown; status?: unknown } | null;
    if (!data || typeof data !== "object") return;
    if (data.type !== "OPENBILICLAW_DOUYIN_FETCH_TAP_INSTALL") return;
    _installMessagesReceived += 1;
    const s = String(data.status ?? "");
    if (s === "installed" || s === "skipped_no_sdk") {
      _lastFetchTapInstallStatus = s;
    }
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Find the homepage's "我" / profile link. Tries multiple selectors
 * because Douyin's UI structure shifts across releases.
 *
 * Returns the element to click, or null if no candidate found.
 */
function findProfileLink(): HTMLElement | null {
  // Most direct: anchor whose href points at /user/self.
  const directHrefSelectors = [
    'a[href="/user/self"]',
    'a[href^="/user/MS4w"]',
    'a[href*="/user/self"]',
  ];
  for (const sel of directHrefSelectors) {
    const el = document.querySelector(sel);
    if (el && "click" in el) return el as HTMLElement;
  }
  // Data-attribute selectors (e2e test selectors Douyin sometimes ships).
  const dataSelectors = ['[data-e2e="profile-icon"]', '[data-e2e="user-tab-self"]'];
  for (const sel of dataSelectors) {
    const el = document.querySelector(sel);
    if (el && "click" in el) return el as HTMLElement;
  }
  // Last resort: anchor or button whose visible text is "我".
  const candidates = Array.from(document.querySelectorAll<HTMLElement>("a, button"));
  for (const el of candidates) {
    if (el.textContent?.trim() === "我") return el;
  }
  return null;
}

/**
 * Find the sub-tab element on the user profile page for a given scope.
 * Returns null for dy_post (which is the default visible tab — no
 * click needed to land on it).
 */
function findScopeSubTab(scope: DouyinScope): HTMLElement | null {
  if (scope === "dy_post") return null;
  const dataSelectors: Record<DouyinScope, string[]> = {
    dy_post: [],
    dy_collect: [
      '[data-e2e="user-favorite-tab"]',
      '[data-e2e="user-tab-favorite_collection"]',
      'a[href*="favorite_collection"]',
    ],
    dy_like: [
      '[data-e2e="user-like-tab"]',
      '[data-e2e="user-tab-like"]',
      'a[href*="showTab=like"]',
    ],
    dy_follow: [
      '[data-e2e="user-following-tab"]',
      '[data-e2e="user-tab-following"]',
      'a[href*="showTab=following"]',
    ],
  };
  for (const sel of dataSelectors[scope]) {
    const el = document.querySelector(sel);
    if (el && "click" in el) return el as HTMLElement;
  }
  // Text fallback. Douyin sub-tab labels:
  //   dy_collect → 收藏
  //   dy_like    → 喜欢
  //   dy_follow  → 关注
  const labelMap: Record<DouyinScope, string> = {
    dy_post: "作品",
    dy_collect: "收藏",
    dy_like: "喜欢",
    dy_follow: "关注",
  };
  const label = labelMap[scope];
  const candidates = Array.from(
    document.querySelectorAll<HTMLElement>('a, button, [role="tab"], [class*="tab"]'),
  );
  for (const el of candidates) {
    if (el.textContent?.trim() === label) return el;
  }
  return null;
}

/**
 * Drive the page from wherever it currently is to the requested
 * scope's view, using **clicks**, not URL writes. This makes the
 * navigation look like user behaviour to Douyin's risk control —
 * direct chrome.tabs.update jumps to /user/self trip the captcha
 * intermediate page (verified 2026-05-08 e2e).
 *
 * Flow per scope:
 *   1. If we're on the homepage (anywhere outside /user/), click
 *      the profile link to land on /user/<sec_uid>. SPA-route, no
 *      document commit, fetch-tap stays.
 *   2. If the requested scope isn't dy_post (which is the default
 *      visible tab), click the sub-tab element. Again SPA-route.
 *
 * Returns true on best-effort success (click(s) attempted), false if
 * we couldn't find any candidate elements — caller can still proceed
 * to scroll loop, just won't have items.
 */
interface ClickToScopeReport {
  page_url: string;
  profile_link_found: boolean;
  sub_tab_found: boolean;
}

async function clickToScope(scope: DouyinScope): Promise<ClickToScopeReport> {
  const report: ClickToScopeReport = {
    page_url: location.href,
    profile_link_found: false,
    sub_tab_found: false,
  };
  const onProfile = location.pathname.startsWith("/user/");
  if (!onProfile) {
    const profileLink = findProfileLink();
    report.profile_link_found = profileLink !== null;
    if (!profileLink) return report;
    profileLink.click();
    // SPA route is async; let React mount the profile page.
    await sleep(2_000);
    report.page_url = location.href;
  }

  if (scope === "dy_post") {
    report.sub_tab_found = true; // default tab, no click needed
    return report;
  }
  const subTab = findScopeSubTab(scope);
  report.sub_tab_found = subTab !== null;
  if (!subTab) return report;
  subTab.click();
  await sleep(1_500); // allow the new sub-tab to fire its first /aweme/.../<scope>/
  report.page_url = location.href;
  return report;
}

async function runScope(msg: ScopeExecuteMessage): Promise<ScopeResultPayload> {
  const { BootstrapItemSink, dyShouldContinueScroll, ingestMainWorldFetchMessage } =
    await loadTaskExecutorHelpers();
  const sink = new BootstrapItemSink({ maxItemsPerScope: msg.max_items_per_scope });
  const allItems: DouyinBootstrapItem[] = [];
  // Per-scope counter: how many OPENBILICLAW_DOUYIN_AWEME_PAGE messages
  // the MAIN-world fetch-tap pushed into this scope's listener window.
  // Distinguished from items count: a message can carry items the sink
  // dedups away or items for the wrong scope, so a non-zero
  // aweme_messages_received with zero items is its own signature.
  let awemeMessagesReceived = 0;

  const onMessage = (event: MessageEvent): void => {
    const data = event?.data as { type?: unknown } | null;
    if (data && typeof data === "object" && data.type === "OPENBILICLAW_DOUYIN_AWEME_PAGE") {
      awemeMessagesReceived += 1;
    }
    const newOnes = ingestMainWorldFetchMessage(event, sink);
    for (const item of newOnes) {
      if (item.scope === msg.scope) allItems.push(item);
    }
  };
  window.addEventListener("message", onMessage);

  let clickReport: ClickToScopeReport = {
    page_url: location.href,
    profile_link_found: false,
    sub_tab_found: false,
  };
  try {
    // Navigate via UI clicks (more natural to Douyin risk control
    // than chrome.tabs.update URL jumps). clickToScope handles both
    // the homepage→profile transition and the sub-tab switch.
    clickReport = await clickToScope(msg.scope);

    // The MAIN-world fetch-tap auto-installs after waitForDouyinSdk
    // resolves. Give it a beat to settle so any pageload-time
    // /aweme/.../<scope>/ that fires AFTER our install gets captured.
    await sleep(POST_INSTALL_SETTLE_MS);

    let stagnantRounds = 0;
    for (let round = 0; round < msg.max_scroll_rounds; round += 1) {
      const beforeCount = sink.scopeCounts()[msg.scope];

      // Trigger Douyin's virtual-list pagination by scrolling down.
      // Use scrollBy with a large delta so even tall card lists move
      // multiple cards' worth per round.
      window.scrollBy({ top: window.innerHeight * 2, behavior: "auto" });
      await sleep(SCROLL_DELAY_MS);

      const afterCount = sink.scopeCounts()[msg.scope];
      stagnantRounds = afterCount > beforeCount ? 0 : stagnantRounds + 1;

      if (
        !dyShouldContinueScroll({
          currentCount: afterCount,
          maxItemsPerScope: msg.max_items_per_scope,
          round: round + 1,
          maxScrollRounds: msg.max_scroll_rounds,
          stagnantRounds,
          maxStagnantScrollRounds: msg.max_stagnant_scroll_rounds,
        })
      ) {
        break;
      }
    }

    return {
      task_id: msg.task_id,
      scope: msg.scope,
      items: allItems,
      scope_count: sink.scopeCounts()[msg.scope],
      status: allItems.length > 0 ? "ok" : "empty",
      debug: {
        fetch_tap_install_status: _lastFetchTapInstallStatus,
        aweme_messages_received: awemeMessagesReceived,
        install_messages_received: _installMessagesReceived,
        inject_status: msg.debug_inject_status,
        page_url: clickReport.page_url,
        profile_link_found: clickReport.profile_link_found,
        sub_tab_found: clickReport.sub_tab_found,
      },
    };
  } catch (err) {
    return {
      task_id: msg.task_id,
      scope: msg.scope,
      items: allItems,
      scope_count: sink.scopeCounts()[msg.scope],
      status: "failed",
      error: String(err),
      debug: {
        fetch_tap_install_status: _lastFetchTapInstallStatus,
        aweme_messages_received: awemeMessagesReceived,
        install_messages_received: _installMessagesReceived,
        inject_status: msg.debug_inject_status,
        page_url: clickReport.page_url,
        profile_link_found: clickReport.profile_link_found,
        sub_tab_found: clickReport.sub_tab_found,
      },
    };
  } finally {
    window.removeEventListener("message", onMessage);
  }
}

export function isValidScopeExecuteMessage(value: unknown): value is ScopeExecuteMessage {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (typeof v.task_id !== "string" || !v.task_id) return false;
  const KNOWN: readonly DouyinScope[] = ["dy_post", "dy_collect", "dy_like", "dy_follow"];
  if (!KNOWN.includes(v.scope as DouyinScope)) return false;
  if (typeof v.max_items_per_scope !== "number") return false;
  if (typeof v.max_scroll_rounds !== "number") return false;
  if (typeof v.max_stagnant_scroll_rounds !== "number") return false;
  return true;
}

export function registerDyScopeExecutor(): void {
  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.onMessage) return;
  chrome.runtime.onMessage.addListener(
    (message: Record<string, unknown>, _sender, sendResponse) => {
      if (message.action !== "DY_SCOPE_EXECUTE") return false;
      const data = message.data;
      if (!isValidScopeExecuteMessage(data)) return false;

      void runScope(data).then((result) => {
        chrome.runtime.sendMessage({ action: "DY_SCOPE_RESULT", data: result }).catch(() => {
          // Service worker may have torn down between scopes; the
          // dispatcher will eventually time out and send status=failed
          // to the backend, so silent here is fine.
        });
      });

      // We don't use sendResponse — return false so the channel closes.
      return false;
    },
  );
}

if (typeof chrome !== "undefined" && chrome.runtime) {
  registerDyScopeExecutor();
  // eslint-disable-next-line no-console
  console.debug("[OpenBiliClaw] dy content script registered (isolated world)");
}
