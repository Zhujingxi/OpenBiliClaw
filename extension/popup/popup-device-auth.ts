import { getBackendBaseUrl } from "./popup-backend-config.js";

export const DEVICE_KEY_STORAGE_KEY = "obc_extension_device_key";
export const SESSION_STORAGE_KEY = "obc_auth_session";
const LEGACY_KEYS = ["obc_auth_password", "obc_auth_token"];
const REFRESH_SKEW_SECONDS = 60;

interface PopupSession {
  token: string;
  expires_at: number;
}

type StorageValues = Record<string, unknown>;
type FetchLike = typeof fetch;

interface StorageAreaLike {
  get(keys: string | string[], callback: (items: StorageValues) => void): unknown;
  set(items: StorageValues, callback: () => void): unknown;
  remove(keys: string | string[], callback: () => void): unknown;
}

interface ExchangeOptions {
  getBaseUrl?: () => Promise<string>;
  fetchImpl?: FetchLike;
  signal?: AbortSignal;
  force?: boolean;
}

interface AuthenticatedFetchOptions {
  signal?: AbortSignal;
  sessionToken?: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function hasThen(value: unknown): value is { then: (...args: unknown[]) => unknown } {
  return ((typeof value === "object" && value !== null) || typeof value === "function") &&
    typeof (value as { then?: unknown }).then === "function";
}

function observeReturnedPromise(
  value: unknown,
  onFulfilled: (result?: unknown) => void,
  onRejected: () => void,
): void {
  if (!hasThen(value)) return;
  const chained = value.then(onFulfilled);
  if (
    ((typeof chained === "object" && chained !== null) || typeof chained === "function") &&
    typeof (chained as { catch?: unknown }).catch === "function"
  ) {
    (chained as { catch: (handler: () => void) => unknown }).catch(onRejected);
  }
}

let cachedSession: PopupSession | null = null;
let sessionLoaded = false;
let refreshInFlight: Promise<string | null> | null = null;
let lastExchangeError = "invalid_device_key";

function storageLocal(): StorageAreaLike | null {
  try {
    const storage: unknown = globalThis.chrome?.storage?.local;
    if (!isRecord(storage)) return null;
    if (
      typeof storage.get !== "function" ||
      typeof storage.set !== "function" ||
      typeof storage.remove !== "function"
    ) return null;
    return storage as unknown as StorageAreaLike;
  } catch {
    return null;
  }
}

function storageGet(keys: string | string[]): Promise<StorageValues> {
  const storage = storageLocal();
  if (!storage?.get) return Promise.resolve({});
  return new Promise<StorageValues>((resolve) => {
    try {
      const maybePromise = storage.get(keys, (items) => resolve(items || {}));
      observeReturnedPromise(
        maybePromise,
        (items) => resolve(isRecord(items) ? items : {}),
        () => resolve({}),
      );
    } catch {
      resolve({});
    }
  });
}

function storageSet(items: StorageValues): Promise<void> {
  const storage = storageLocal();
  if (!storage?.set) return Promise.resolve();
  return new Promise<void>((resolve) => {
    try {
      const maybePromise = storage.set(items, () => resolve());
      observeReturnedPromise(maybePromise, () => resolve(), () => resolve());
    } catch {
      resolve();
    }
  });
}

function storageRemove(keys: string | string[]): Promise<void> {
  const storage = storageLocal();
  if (!storage?.remove) return Promise.resolve();
  return new Promise<void>((resolve) => {
    try {
      const maybePromise = storage.remove(keys, () => resolve());
      observeReturnedPromise(maybePromise, () => resolve(), () => resolve());
    } catch {
      resolve();
    }
  });
}

function parseSession(value: unknown): PopupSession | null {
  if (!isRecord(value)) return null;
  const token = typeof value.token === "string" ? value.token.trim() : "";
  const expiresAt = Number(value.expires_at);
  return token && Number.isFinite(expiresAt) && expiresAt > 0
    ? { token, expires_at: expiresAt }
    : null;
}

async function loadSession(): Promise<PopupSession | null> {
  if (sessionLoaded) return cachedSession;
  const items = await storageGet(SESSION_STORAGE_KEY);
  cachedSession = parseSession(items[SESSION_STORAGE_KEY]);
  sessionLoaded = true;
  return cachedSession;
}

async function saveSession(session: PopupSession): Promise<void> {
  cachedSession = parseSession(session);
  if (!cachedSession) throw new Error("invalid_device_session");
  sessionLoaded = true;
  await storageSet({ [SESSION_STORAGE_KEY]: cachedSession });
}

export async function clearPopupSession(): Promise<void> {
  cachedSession = null;
  sessionLoaded = true;
  await storageRemove(SESSION_STORAGE_KEY);
}

export async function readPopupSessionToken(): Promise<string | null> {
  const session = await loadSession();
  return session && session.expires_at > Date.now() / 1000 ? session.token : null;
}

async function exchange(options: ExchangeOptions = {}, keyOverride = ""): Promise<string | null> {
  const items = keyOverride ? {} : await storageGet(DEVICE_KEY_STORAGE_KEY);
  const key = keyOverride || (typeof items[DEVICE_KEY_STORAGE_KEY] === "string"
    ? items[DEVICE_KEY_STORAGE_KEY].trim() : "");
  if (!key) {
    lastExchangeError = "missing_device_key";
    return null;
  }
  const base = await (options.getBaseUrl || getBackendBaseUrl)();
  const doFetch = options.fetchImpl || globalThis.fetch.bind(globalThis);
  let response: Response;
  try {
    response = await doFetch(`${base}/auth/extension-token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
      signal: options.signal,
    });
  } catch (error) {
    if (options.signal?.aborted) throw options.signal.reason || error;
    lastExchangeError = "backend_unreachable";
    return null;
  }
  if (!response.ok) {
    try {
      const payload: unknown = await response.json();
      lastExchangeError = String(
        isRecord(payload) ? payload.error || "invalid_device_key" : "invalid_device_key",
      );
    } catch {
      lastExchangeError = "invalid_device_key";
    }
    await clearPopupSession();
    return null;
  }
  const payload: unknown = await response.json();
  if (
    !isRecord(payload) ||
    !payload.ok ||
    typeof payload.token !== "string" ||
    !payload.token ||
    !Number.isFinite(Number(payload.expires_at))
  ) {
    lastExchangeError = "invalid_device_key";
    await clearPopupSession();
    return null;
  }
  const session = { token: payload.token, expires_at: Number(payload.expires_at) };
  if (keyOverride) {
    cachedSession = session;
    sessionLoaded = true;
    // One storage transaction prevents the service worker from observing a
    // device key without its freshly exchanged session and racing a duplicate
    // exchange in another extension context.
    await storageSet({
      [DEVICE_KEY_STORAGE_KEY]: keyOverride,
      [SESSION_STORAGE_KEY]: session,
    });
  } else {
    await saveSession(session);
  }
  lastExchangeError = "";
  await storageRemove(LEGACY_KEYS);
  return payload.token;
}

export async function ensurePopupSession(options: ExchangeOptions = {}): Promise<string | null> {
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

export async function pairDeviceKey(
  key: unknown,
  options: ExchangeOptions = {},
): Promise<string> {
  const normalized = String(key || "").trim();
  if (!normalized) throw new Error("missing_device_key");
  cachedSession = null;
  sessionLoaded = true;
  if (refreshInFlight) await refreshInFlight;
  refreshInFlight = exchange(options, normalized).finally(() => {
    refreshInFlight = null;
  });
  const token = await refreshInFlight;
  if (!token) {
    await storageRemove(DEVICE_KEY_STORAGE_KEY);
    throw new Error(lastExchangeError || "invalid_device_key");
  }
  return token;
}

function withBearer(init: RequestInit, token: string | null): RequestInit {
  const original = init?.headers;
  if (original instanceof Headers) {
    const headers = new Headers(original);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    else headers.delete("Authorization");
    return { ...init, headers };
  }
  const headers: Record<string, string> = Array.isArray(original)
    ? Object.fromEntries(original)
    : { ...((original || {}) as Record<string, string>) };
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === "authorization") delete headers[key];
  }
  if (token) headers.Authorization = `Bearer ${token}`;
  return { ...init, headers };
}

export async function popupAuthenticatedFetch(
  url: RequestInfo | URL,
  init: RequestInit = {},
  fetchImpl: FetchLike = globalThis.fetch.bind(globalThis),
  options: AuthenticatedFetchOptions = {},
): Promise<Response> {
  const signal = options.signal || init?.signal || undefined;
  const token = Object.hasOwn(options, "sessionToken")
    ? options.sessionToken
    : await ensurePopupSession({ fetchImpl, signal });
  const first = await fetchImpl(url, withBearer(init, token ?? null));
  if (first.status !== 401 || !token) return first;
  const current = await readPopupSessionToken();
  const refreshed = current && current !== token
    ? current
    : await ensurePopupSession({ force: true, fetchImpl, signal });
  if (!refreshed) return first;
  return fetchImpl(url, withBearer(init, refreshed));
}

export function __resetPopupDeviceAuthForTests(): void {
  cachedSession = null;
  sessionLoaded = false;
  refreshInFlight = null;
  lastExchangeError = "invalid_device_key";
}
