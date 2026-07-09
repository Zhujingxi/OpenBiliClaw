import { getBackendBaseUrl } from "./popup-backend-config.js";

export const DEVICE_KEY_STORAGE_KEY = "obc_extension_device_key";
export const SESSION_STORAGE_KEY = "obc_auth_session";
const LEGACY_KEYS = ["obc_auth_password", "obc_auth_token"];
const REFRESH_SKEW_SECONDS = 60;

let cachedSession = null;
let sessionLoaded = false;
let refreshInFlight = null;
let lastExchangeError = "invalid_device_key";

function storageLocal() {
  try {
    return globalThis.chrome?.storage?.local ?? null;
  } catch {
    return null;
  }
}

function storageGet(keys) {
  const storage = storageLocal();
  if (!storage?.get) return Promise.resolve({});
  return new Promise((resolve) => {
    try {
      const maybePromise = storage.get(keys, (items) => resolve(items || {}));
      if (maybePromise?.then) maybePromise.then(resolve).catch(() => resolve({}));
    } catch {
      resolve({});
    }
  });
}

function storageSet(items) {
  const storage = storageLocal();
  if (!storage?.set) return Promise.resolve();
  return new Promise((resolve) => {
    try {
      const maybePromise = storage.set(items, () => resolve());
      if (maybePromise?.then) maybePromise.then(resolve).catch(resolve);
    } catch {
      resolve();
    }
  });
}

function storageRemove(keys) {
  const storage = storageLocal();
  if (!storage?.remove) return Promise.resolve();
  return new Promise((resolve) => {
    try {
      const maybePromise = storage.remove(keys, () => resolve());
      if (maybePromise?.then) maybePromise.then(resolve).catch(resolve);
    } catch {
      resolve();
    }
  });
}

function parseSession(value) {
  if (!value || typeof value !== "object") return null;
  const token = typeof value.token === "string" ? value.token.trim() : "";
  const expiresAt = Number(value.expires_at);
  return token && Number.isFinite(expiresAt) && expiresAt > 0
    ? { token, expires_at: expiresAt }
    : null;
}

async function loadSession() {
  if (sessionLoaded) return cachedSession;
  const items = await storageGet(SESSION_STORAGE_KEY);
  cachedSession = parseSession(items[SESSION_STORAGE_KEY]);
  sessionLoaded = true;
  return cachedSession;
}

async function saveSession(session) {
  cachedSession = parseSession(session);
  if (!cachedSession) throw new Error("invalid_device_session");
  sessionLoaded = true;
  await storageSet({ [SESSION_STORAGE_KEY]: cachedSession });
}

export async function clearPopupSession() {
  cachedSession = null;
  sessionLoaded = true;
  await storageRemove(SESSION_STORAGE_KEY);
}

export async function readPopupSessionToken() {
  const session = await loadSession();
  return session && session.expires_at > Date.now() / 1000 ? session.token : null;
}

async function exchange(options = {}) {
  const items = await storageGet(DEVICE_KEY_STORAGE_KEY);
  const key = typeof items[DEVICE_KEY_STORAGE_KEY] === "string"
    ? items[DEVICE_KEY_STORAGE_KEY].trim() : "";
  if (!key) {
    lastExchangeError = "missing_device_key";
    return null;
  }
  const base = await (options.getBaseUrl || getBackendBaseUrl)();
  const doFetch = options.fetchImpl || globalThis.fetch.bind(globalThis);
  let response;
  try {
    response = await doFetch(`${base}/auth/extension-token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
  } catch {
    lastExchangeError = "backend_unreachable";
    return null;
  }
  if (!response.ok) {
    try {
      const payload = await response.json();
      lastExchangeError = String(payload?.error || "invalid_device_key");
    } catch {
      lastExchangeError = "invalid_device_key";
    }
    await clearPopupSession();
    return null;
  }
  const payload = await response.json();
  if (!payload?.ok || !payload?.token || !Number.isFinite(Number(payload?.expires_at))) {
    lastExchangeError = "invalid_device_key";
    await clearPopupSession();
    return null;
  }
  await saveSession({ token: payload.token, expires_at: Number(payload.expires_at) });
  lastExchangeError = "";
  await storageRemove(LEGACY_KEYS);
  return payload.token;
}

export async function ensurePopupSession(options = {}) {
  const session = await loadSession();
  if (!options.force && session && session.expires_at > Date.now() / 1000 + REFRESH_SKEW_SECONDS) {
    return session.token;
  }
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = exchange(options).finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

export async function pairDeviceKey(key, options = {}) {
  const normalized = String(key || "").trim();
  if (!normalized) throw new Error("missing_device_key");
  await storageSet({ [DEVICE_KEY_STORAGE_KEY]: normalized });
  await clearPopupSession();
  const token = await ensurePopupSession({ ...options, force: true });
  if (!token) {
    await storageRemove(DEVICE_KEY_STORAGE_KEY);
    throw new Error(lastExchangeError || "invalid_device_key");
  }
  return token;
}

function withBearer(init, token) {
  const original = init?.headers;
  if (original instanceof Headers) {
    const headers = new Headers(original);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    else headers.delete("Authorization");
    return { ...init, headers };
  }
  const headers = Array.isArray(original)
    ? Object.fromEntries(original)
    : { ...(original || {}) };
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === "authorization") delete headers[key];
  }
  if (token) headers.Authorization = `Bearer ${token}`;
  return { ...init, headers };
}

export async function popupAuthenticatedFetch(
  url,
  init = {},
  fetchImpl = globalThis.fetch.bind(globalThis),
  options = {},
) {
  const token = Object.hasOwn(options, "sessionToken")
    ? options.sessionToken
    : await ensurePopupSession({ fetchImpl });
  const first = await fetchImpl(url, withBearer(init, token));
  if (first.status !== 401 || !token) return first;
  const current = await readPopupSessionToken();
  const refreshed = current && current !== token
    ? current
    : await ensurePopupSession({ force: true, fetchImpl });
  if (!refreshed) return first;
  return fetchImpl(url, withBearer(init, refreshed));
}

export function __resetPopupDeviceAuthForTests() {
  cachedSession = null;
  sessionLoaded = false;
  refreshInFlight = null;
  lastExchangeError = "invalid_device_key";
}
