/** Persistent device credential and short-lived extension session storage. */

export const DEVICE_KEY_STORAGE_KEY = "obc_extension_device_key";
export const SESSION_STORAGE_KEY = "obc_auth_session";
const LEGACY_KEYS = ["obc_auth_password", "obc_auth_token"];

export interface DeviceSession {
  token: string;
  expires_at: number;
}

interface ChromeStorageLike {
  get?: (key: string | string[], cb: (items: Record<string, unknown>) => void) => void;
  set?: (items: Record<string, unknown>, cb?: () => void) => void;
  remove?: (key: string | string[], cb?: () => void) => void;
}

let cachedSession: DeviceSession | null = null;
let sessionLoaded = false;

function getStorage(): ChromeStorageLike | null {
  try {
    const chromeApi = (globalThis as { chrome?: { storage?: { local?: ChromeStorageLike } } })
      .chrome;
    return chromeApi?.storage?.local ?? null;
  } catch {
    return null;
  }
}

function storageGet(keys: string | string[]): Promise<Record<string, unknown>> {
  const storage = getStorage();
  if (!storage?.get) return Promise.resolve({});
  return new Promise((resolve) => {
    try {
      storage.get?.(keys, (items) => resolve(items ?? {}));
    } catch {
      resolve({});
    }
  });
}

function storageSet(items: Record<string, unknown>): Promise<void> {
  const storage = getStorage();
  if (!storage?.set) return Promise.resolve();
  return new Promise((resolve) => {
    try {
      storage.set?.(items, () => resolve());
    } catch {
      resolve();
    }
  });
}

function storageRemove(keys: string | string[]): Promise<void> {
  const storage = getStorage();
  if (!storage?.remove) return Promise.resolve();
  return new Promise((resolve) => {
    try {
      storage.remove?.(keys, () => resolve());
    } catch {
      resolve();
    }
  });
}

function parseSession(value: unknown): DeviceSession | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const token = typeof raw.token === "string" ? raw.token.trim() : "";
  const expiresAt = typeof raw.expires_at === "number" ? raw.expires_at : Number(raw.expires_at);
  if (!token || !Number.isFinite(expiresAt) || expiresAt <= 0) return null;
  return { token, expires_at: expiresAt };
}

export async function getDeviceKey(): Promise<string | null> {
  const items = await storageGet(DEVICE_KEY_STORAGE_KEY);
  const value = items[DEVICE_KEY_STORAGE_KEY];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export async function setDeviceKey(key: string): Promise<void> {
  await storageSet({ [DEVICE_KEY_STORAGE_KEY]: key.trim() });
}

export async function loadSession(): Promise<DeviceSession | null> {
  if (sessionLoaded) return cachedSession;
  const items = await storageGet(SESSION_STORAGE_KEY);
  cachedSession = parseSession(items[SESSION_STORAGE_KEY]);
  sessionLoaded = true;
  return cachedSession;
}

export async function saveSession(session: DeviceSession): Promise<void> {
  const parsed = parseSession(session);
  if (!parsed) throw new Error("invalid_device_session");
  cachedSession = parsed;
  sessionLoaded = true;
  await storageSet({ [SESSION_STORAGE_KEY]: parsed });
}

export async function clearSession(): Promise<void> {
  cachedSession = null;
  sessionLoaded = true;
  await storageRemove(SESSION_STORAGE_KEY);
}

export async function clearDeviceKey(): Promise<void> {
  await storageRemove(DEVICE_KEY_STORAGE_KEY);
  await clearSession();
}

export async function clearLegacyCredentials(): Promise<void> {
  await storageRemove(LEGACY_KEYS);
}

export function __resetTokenStoreForTests(): void {
  cachedSession = null;
  sessionLoaded = false;
}
