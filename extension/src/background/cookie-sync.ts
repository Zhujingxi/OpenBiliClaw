/**
 * OpenBiliClaw — Bilibili cookie auto-sync.
 *
 * Reads the user's live bilibili.com cookies via chrome.cookies.getAll()
 * and POSTs them to the local backend so the user never has to do the
 * F12 → Network → copy → paste dance. Triggers:
 *
 *   - on extension install / update
 *   - on browser startup
 *   - whenever chrome.cookies.onChanged fires for a bilibili.com cookie
 *     (debounced, so a login that touches 8 cookies = 1 sync, not 8)
 *   - hourly fallback alarm, in case onChanged misses something
 *
 * Backend endpoint (added in v0.3.12): POST /api/bilibili/cookie. The
 * backend validates the cookie against api.bilibili.com/x/web-interface/nav
 * before persisting, so we never overwrite a working cookie with garbage.
 */

const COOKIE_SYNC_URL = "http://127.0.0.1:8420/api/bilibili/cookie";
const COOKIE_SYNC_ALARM = "openbiliclaw-cookie-sync";
const COOKIE_SYNC_DEBOUNCE_MS = 2_000;
const COOKIE_SYNC_REFRESH_MINUTES = 60;
const COOKIE_SYNC_RETRY_MINUTES = 1;

/** Critical cookie names — without these, the backend can't call B 站 API. */
const REQUIRED_COOKIE_NAMES = ["SESSDATA", "bili_jct", "DedeUserID"];

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
    const response = await fetch(COOKIE_SYNC_URL, {
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
    };
    if (result.ok && result.authenticated) {
      console.log(
        `[openbiliclaw] cookie synced via ${source}` +
          (result.username ? ` (logged in as ${result.username})` : ""),
      );
      scheduleHourlyCookieSync();
    }
    return result.ok;
  } catch (err) {
    // Backend not running, network blocked, etc — silent retry on next tick.
    console.warn("[openbiliclaw] cookie sync failed:", err);
    scheduleCookieSyncAlarm(COOKIE_SYNC_RETRY_MINUTES);
    return false;
  }
}

/**
 * Handle backend runtime-stream events that explicitly ask the extension
 * to push the current Bilibili cookie now.
 */
export function handleCookieSyncRuntimeEvent(event: Record<string, unknown>): boolean {
  if (String(event.type ?? "") !== "bilibili_cookie_sync_requested") {
    return false;
  }
  void syncBilibiliCookieToBackend("runtime-stream-request");
  return true;
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

  // React to login / logout / refresh.
  chromeApi.cookies.onChanged.addListener((changeInfo) => {
    const domain = (changeInfo.cookie.domain || "").toLowerCase();
    if (!domain.endsWith("bilibili.com")) {
      return;
    }
    if (!REQUIRED_COOKIE_NAMES.includes(changeInfo.cookie.name)) {
      // Many bilibili.com cookies churn for tracking. Only the
      // session-bearing ones matter for our use case.
      return;
    }
    scheduleCookieSync(changeInfo.removed ? "logout" : "cookies-onchange");
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
  return true;
}
