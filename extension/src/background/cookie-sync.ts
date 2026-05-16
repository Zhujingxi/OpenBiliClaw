/**
 * OpenBiliClaw — browser cookie auto-sync.
 *
 * Reads the user's live bilibili.com / douyin.com cookies via
 * chrome.cookies.getAll() and POSTs them to the local backend so the user
 * does not have to do the F12 → Network → copy → paste dance. Triggers:
 *
 *   - on extension install / update
 *   - on browser startup
 *   - whenever chrome.cookies.onChanged fires for a relevant site cookie
 *     (debounced, so a login that touches many cookies = 1 sync round)
 *   - hourly fallback alarm, in case onChanged misses something
 *
 * Backend endpoints: POST /api/bilibili/cookie validates against Bilibili
 * nav before persisting; POST /api/sources/dy/cookie stores the browser
 * Douyin cookie for direct discovery smoke / recall.
 */

// .ts extension: see service-worker.ts for the node:test resolver rationale.
import { apiUrl } from "../shared/backend-endpoint.ts";

const COOKIE_SYNC_ALARM = "openbiliclaw-cookie-sync";
const COOKIE_SYNC_DEBOUNCE_MS = 2_000;
const COOKIE_SYNC_REFRESH_MINUTES = 60;
// HTTP-level failures (5xx, timeout, backend down): retry quickly.
const COOKIE_SYNC_RETRY_MINUTES = 1;
// Backend reachable but B站 validation network-failed (proxy / DNS):
// retry every 5 min — usually clears once user's network calms down,
// but don't hammer either.
const COOKIE_SYNC_VALIDATION_NETWORK_RETRY_MINUTES = 5;
// Backend says cookie itself is invalid / expired: only the user's
// next bilibili.com login fixes this. Quiet hourly retry as
// belt-and-braces in case the cookie was edited externally.
const COOKIE_SYNC_COOKIE_INVALID_RETRY_MINUTES = 60;

/** Critical cookie names — without these, the backend can't call B 站 API. */
const REQUIRED_COOKIE_NAMES = ["SESSDATA", "bili_jct", "DedeUserID"];
// Douyin's Web APIs are soft-failure heavy: bad / incomplete cookies
// often still get HTTP 200 with empty data. Modern logged-in jars do not
// always expose msToken, so we accept any session / passport signal and
// still send the full header because ttwid / odin / device cookies help.
const DOUYIN_AUTH_SIGNAL_COOKIE_NAMES = [
  "msToken",
  "sessionid",
  "sessionid_ss",
  "sid_guard",
  "sid_tt",
  "uid_tt",
  "uid_tt_ss",
  "passport_assist_user",
  "passport_mfa_token",
  "passport_csrf_token",
  "odin_tt",
];
const IMPORTANT_DOUYIN_COOKIE_NAMES = [
  "msToken",
  "ttwid",
  "sessionid",
  "sid_guard",
  "sid_tt",
  "uid_tt",
  "passport_csrf_token",
  "passport_auth_status",
  "odin_tt",
];

let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let cookieSyncStarted = false;

function getChromeApi(): typeof chrome | null {
  if (typeof chrome === "undefined") {
    return null;
  }
  return chrome;
}

function scheduleCookieSyncAlarm(minutes: number): void {
  const chromeApi = getChromeApi();
  if (!chromeApi?.alarms?.create) return;
  chromeApi.alarms.create(COOKIE_SYNC_ALARM, {
    delayInMinutes: minutes,
    periodInMinutes: minutes,
  });
}

function scheduleHourlyCookieSync(): void {
  const chromeApi = getChromeApi();
  if (!chromeApi?.alarms?.create) return;
  chromeApi.alarms.create(COOKIE_SYNC_ALARM, {
    periodInMinutes: COOKIE_SYNC_REFRESH_MINUTES,
  });
}

/**
 * Read all bilibili.com cookies and return them as a single Cookie
 * header value (`SESSDATA=...; bili_jct=...; DedeUserID=...`).
 *
 * Returns null when the user isn't logged in (i.e. one of the
 * required cookies is missing). We deliberately do NOT push partial
 * cookies — the backend would fail validation and we'd send a useless
 * round trip.
 */
export async function readBilibiliCookieHeader(): Promise<string | null> {
  const chromeApi = getChromeApi();
  if (!chromeApi?.cookies?.getAll) {
    return null;
  }
  // domain="bilibili.com" matches both the bare domain and any
  // subdomain (passport.bilibili.com, www.bilibili.com, etc).
  const cookies = await chromeApi.cookies.getAll({ domain: "bilibili.com" });
  const have = new Set(cookies.map((c) => c.name));
  for (const required of REQUIRED_COOKIE_NAMES) {
    if (!have.has(required)) {
      return null;
    }
  }
  // Render in the standard Cookie-header form. Order doesn't matter
  // to the B 站 API but we keep it stable for log readability.
  return cookies
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");
}

/**
 * Read all douyin.com cookies and return them as a Cookie header.
 *
 * We do not attempt to prove login here. Douyin frequently returns
 * HTTP 200 + empty data for soft anti-bot / logged-out states, so the
 * backend persists the browser cookie and discovery smoke is the source
 * of truth for whether the current jar can actually fetch candidates.
 */
export async function readDouyinCookieHeader(): Promise<string | null> {
  const chromeApi = getChromeApi();
  if (!chromeApi?.cookies?.getAll) {
    return null;
  }
  const cookies = (await chromeApi.cookies.getAll({ domain: "douyin.com" })).filter(
    (cookie) => cookie.name && cookie.value,
  );
  const have = new Set(cookies.map((c) => c.name));
  if (!DOUYIN_AUTH_SIGNAL_COOKIE_NAMES.some((name) => have.has(name))) {
    return null;
  }
  return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
}

/**
 * POST the current cookie to the backend if and only if the user is
 * actually logged in. Returns whether the sync round-tripped okay.
 *
 * Errors (network, 4xx, validation) are swallowed and logged so a flaky
 * backend never breaks the rest of the extension. The next debounce
 * tick or hourly alarm will retry.
 */
export async function syncBilibiliCookieToBackend(
  source: string = "extension",
): Promise<boolean> {
  const cookieHeader = await readBilibiliCookieHeader();
  if (!cookieHeader) {
    // User isn't logged in — don't pester the backend.
    return false;
  }
  try {
    const response = await fetch(await apiUrl("/bilibili/cookie"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        cookie: cookieHeader,
        source,
        validate_with_bilibili: true,
      }),
    });
    if (!response.ok) {
      console.warn(`[openbiliclaw] cookie sync HTTP ${response.status}`);
      scheduleCookieSyncAlarm(COOKIE_SYNC_RETRY_MINUTES);
      return false;
    }
    const result = (await response.json()) as {
      ok: boolean;
      authenticated: boolean;
      username?: string;
      error_code?: string;
      message?: string;
    };
    if (result.ok && result.authenticated) {
      console.log(
        `[openbiliclaw] cookie synced via ${source}` +
          (result.username ? ` (logged in as ${result.username})` : ""),
      );
      scheduleHourlyCookieSync();
      return true;
    }
    // Backend returned 200 but rejected the cookie. Use error_code to
    // pick a smart retry interval: validation network errors clear
    // quickly, but expired cookies need a real bilibili.com re-login
    // to fix.
    const errorCode = String(result.error_code || "").toLowerCase();
    const message = String(result.message || "");
    if (errorCode === "validation_network") {
      console.warn(
        `[openbiliclaw] cookie validation network-failed (${source}): ${message} — retry in ${COOKIE_SYNC_VALIDATION_NETWORK_RETRY_MINUTES}min`,
      );
      scheduleCookieSyncAlarm(COOKIE_SYNC_VALIDATION_NETWORK_RETRY_MINUTES);
    } else if (errorCode === "cookie_invalid") {
      console.warn(
        `[openbiliclaw] cookie invalid / expired (${source}): ${message} — waiting for next bilibili.com login (or hourly retry)`,
      );
      scheduleCookieSyncAlarm(COOKIE_SYNC_COOKIE_INVALID_RETRY_MINUTES);
    } else {
      // Unknown / legacy backend without error_code — fall back to a
      // moderate 5-min retry so we don't sit on a 1-hour gap by accident.
      console.warn(
        `[openbiliclaw] cookie sync rejected (${source}): code=${errorCode || "(unset)"} message=${message} — retry in 5min`,
      );
      scheduleCookieSyncAlarm(COOKIE_SYNC_VALIDATION_NETWORK_RETRY_MINUTES);
    }
    return false;
  } catch (err) {
    // Backend not running, network blocked, etc — silent retry on next tick.
    console.warn("[openbiliclaw] cookie sync failed:", err);
    scheduleCookieSyncAlarm(COOKIE_SYNC_RETRY_MINUTES);
    return false;
  }
}

export async function syncDouyinCookieToBackend(
  source: string = "extension",
): Promise<boolean> {
  const cookieHeader = await readDouyinCookieHeader();
  if (!cookieHeader) {
    return false;
  }
  try {
    const response = await fetch(await apiUrl("/sources/dy/cookie"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        cookie: cookieHeader,
        source,
      }),
    });
    if (!response.ok) {
      console.warn(`[openbiliclaw] douyin cookie sync HTTP ${response.status}`);
      scheduleCookieSyncAlarm(COOKIE_SYNC_RETRY_MINUTES);
      return false;
    }
    const result = (await response.json()) as {
      ok: boolean;
      has_cookie: boolean;
      error_code?: string;
      message?: string;
    };
    if (result.ok && result.has_cookie) {
      console.log(`[openbiliclaw] douyin cookie synced via ${source}`);
      scheduleHourlyCookieSync();
      return true;
    }
    const message = String(result.message || "");
    console.warn(`[openbiliclaw] douyin cookie sync rejected (${source}): ${message}`);
    scheduleCookieSyncAlarm(COOKIE_SYNC_VALIDATION_NETWORK_RETRY_MINUTES);
    return false;
  } catch (err) {
    console.warn("[openbiliclaw] douyin cookie sync failed:", err);
    scheduleCookieSyncAlarm(COOKIE_SYNC_RETRY_MINUTES);
    return false;
  }
}

/**
 * Handle backend runtime-stream events that explicitly ask the extension
 * to push the current site cookie now.
 */
export function handleCookieSyncRuntimeEvent(event: Record<string, unknown>): boolean {
  const eventType = String(event.type ?? "");
  if (eventType === "bilibili_cookie_sync_requested") {
    void syncBilibiliCookieToBackend("runtime-stream-request");
    return true;
  }
  if (eventType === "douyin_cookie_sync_requested") {
    void syncDouyinCookieToBackend("runtime-stream-request");
    return true;
  }
  return false;
}

/**
 * Debounced sync. A login on bilibili.com fires onChanged for every
 * cookie individually — without debouncing we'd POST 6-10 times per
 * second-long login.
 */
function scheduleCookieSync(source: string): void {
  if (debounceTimer !== null) {
    clearTimeout(debounceTimer);
  }
  debounceTimer = setTimeout(() => {
    debounceTimer = null;
    void syncBilibiliCookieToBackend(source);
    void syncDouyinCookieToBackend(source);
  }, COOKIE_SYNC_DEBOUNCE_MS);
}

/**
 * Wire up the listeners. Idempotent — safe to call from both
 * onInstalled and onStartup.
 */
export function startCookieSync(): void {
  const chromeApi = getChromeApi();
  if (!chromeApi?.cookies?.onChanged) {
    // Service worker without the cookies permission — silently no-op.
    return;
  }
  if (cookieSyncStarted) {
    return;
  }
  cookieSyncStarted = true;

  // Initial best-effort sync. The user might have been logged in before
  // installing the extension; this catches that case.
  void syncBilibiliCookieToBackend("startup");
  void syncDouyinCookieToBackend("startup");

  // React to login / logout / refresh.
  chromeApi.cookies.onChanged.addListener((changeInfo) => {
    const domain = (changeInfo.cookie.domain || "").toLowerCase();
    if (domain.endsWith("bilibili.com")) {
      if (!REQUIRED_COOKIE_NAMES.includes(changeInfo.cookie.name)) {
        // Many bilibili.com cookies churn for tracking. Only the
        // session-bearing ones matter for our use case.
        return;
      }
      scheduleCookieSync(changeInfo.removed ? "logout" : "cookies-onchange");
      return;
    }
    if (domain.endsWith("douyin.com")) {
      if (!IMPORTANT_DOUYIN_COOKIE_NAMES.includes(changeInfo.cookie.name)) {
        return;
      }
      scheduleCookieSync(changeInfo.removed ? "douyin-logout" : "douyin-cookies-onchange");
    }
  });

  // Hourly belt-and-braces refresh in case onChanged drops events while
  // the service worker is unloaded. Failed POSTs temporarily tighten this
  // to a 1-minute retry until the backend accepts the cookie.
  scheduleHourlyCookieSync();
}

/**
 * Hook into the existing chrome.alarms.onAlarm dispatcher in the
 * service worker. Returns true when the alarm name matched and was
 * handled here.
 */
export function handleCookieSyncAlarm(alarmName: string): boolean {
  if (alarmName !== COOKIE_SYNC_ALARM) {
    return false;
  }
  void syncBilibiliCookieToBackend("hourly-alarm");
  void syncDouyinCookieToBackend("hourly-alarm");
  return true;
}
