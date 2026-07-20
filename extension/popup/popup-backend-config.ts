/**
 * OpenBiliClaw popup — configurable backend endpoint.
 *
 * Mirrors src/shared/backend-endpoint.ts for popup modules (which load
 * straight from popup/ as native JS, not via the esbuild bundle). Both
 * sides read & write the same chrome.storage.local key, so a change in
 * the popup is picked up by the service worker via chrome.storage.onChanged.
 *
 * Default endpoint is 127.0.0.1:8420. Store-distributed builds only declare
 * loopback backend host permissions; custom LAN/self-hosted origins require a
 * build or future optional-permission flow that grants that origin. Users can
 * still override the port to dodge local port conflicts on Windows (Hyper-V /
 * WSL / Docker reserve random local ports — 18080, 19090, 13000 are common safe
 * choices).
 */

export const DEFAULT_BACKEND_HOST = "127.0.0.1";
export const DEFAULT_BACKEND_PORT = 8420;
export const DEFAULT_BACKEND_SCHEME = "http";
export const BACKEND_ENDPOINT_STORAGE_KEY = "popup_backend_endpoint";

export interface BackendEndpoint {
  scheme: "http" | "https";
  host: string;
  port: number;
}

interface PermissionsLike {
  contains(
    details: chrome.permissions.Permissions,
    callback: (granted: boolean) => void,
  ): void;
  request(
    details: chrome.permissions.Permissions,
    callback: (granted: boolean) => void,
  ): void;
}

interface UpdateBackendEndpointOptions {
  permissionsApi?: PermissionsLike | null;
}

type EndpointSubscriber = (endpoint: BackendEndpoint) => void;

const DEFAULT_ENDPOINT: BackendEndpoint = {
  scheme: DEFAULT_BACKEND_SCHEME,
  host: DEFAULT_BACKEND_HOST,
  port: DEFAULT_BACKEND_PORT,
};

let cached = { ...DEFAULT_ENDPOINT };
let initialized = false;
let initPromise: Promise<BackendEndpoint> | null = null;
let storageListenerInstalled = false;
const subscribers = new Set<EndpointSubscriber>();

function getStorageLocal(): chrome.storage.StorageArea | null {
  try {
    return globalThis.chrome?.storage?.local ?? null;
  } catch {
    return null;
  }
}

function getStorageOnChanged(): typeof chrome.storage.onChanged | null {
  try {
    return globalThis.chrome?.storage?.onChanged ?? null;
  } catch {
    return null;
  }
}

function parseBackendPort(value: unknown): number | null {
  if (typeof value === "number" && Number.isInteger(value)) {
    return value >= 1 && value <= 65535 ? value : null;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const trimmed = value.trim();
    if (!/^[0-9]+$/.test(trimmed)) {
      return null;
    }
    const parsed = Number(trimmed);
    return Number.isInteger(parsed) && parsed >= 1 && parsed <= 65535 ? parsed : null;
  }
  return null;
}

export function isValidBackendPort(value: unknown): boolean {
  return parseBackendPort(value) !== null;
}

function coercePort(value: unknown): number {
  return parseBackendPort(value) ?? DEFAULT_BACKEND_PORT;
}

function sanitizeEndpoint(raw: unknown): BackendEndpoint {
  if (typeof raw !== "object" || raw === null) {
    return { ...DEFAULT_ENDPOINT };
  }
  const value = raw as Record<string, unknown>;
  const hostRaw = typeof value.host === "string" ? value.host.trim() : "";
  return {
    scheme: value.scheme === "https" ? "https" : "http",
    host: hostRaw || DEFAULT_BACKEND_HOST,
    port: coercePort(value.port),
  };
}

async function loadFromStorage(): Promise<BackendEndpoint> {
  const storage = getStorageLocal();
  if (typeof storage?.get !== "function") {
    return { ...cached };
  }
  return new Promise((resolve) => {
    try {
      storage.get(BACKEND_ENDPOINT_STORAGE_KEY, (items) => {
        const stored = items?.[BACKEND_ENDPOINT_STORAGE_KEY];
        resolve(stored === undefined ? { ...cached } : sanitizeEndpoint(stored));
      });
    } catch {
      resolve({ ...cached });
    }
  });
}

function installStorageChangeListener(): void {
  if (storageListenerInstalled) return;
  const onChanged = getStorageOnChanged();
  if (typeof onChanged?.addListener !== "function") return;
  try {
    onChanged.addListener((changes, area) => {
      if (area !== "local") return;
      const change = changes?.[BACKEND_ENDPOINT_STORAGE_KEY];
      if (!change) return;
      const next = sanitizeEndpoint(change.newValue);
      cached = next;
      initialized = true;
      for (const cb of subscribers) {
        try {
          cb(next);
        } catch {
          // Ignore subscriber failures so peers still get notified.
        }
      }
    });
    storageListenerInstalled = true;
  } catch {
    // chrome.storage.onChanged unavailable (tests).
  }
}

async function ensureLoaded(): Promise<BackendEndpoint> {
  if (initialized) return cached;
  if (initPromise) return initPromise;
  initPromise = (async () => {
    const endpoint = await loadFromStorage();
    cached = endpoint;
    initialized = true;
    installStorageChangeListener();
    return endpoint;
  })();
  return initPromise;
}

export async function getBackendEndpointConfig(): Promise<BackendEndpoint> {
  return ensureLoaded();
}

export async function getBackendOrigin(): Promise<string> {
  const ep = await ensureLoaded();
  return `${ep.scheme}://${ep.host}:${ep.port}`;
}

export async function getBackendBaseUrl(): Promise<string> {
  const ep = await ensureLoaded();
  return `${ep.scheme}://${ep.host}:${ep.port}/api`;
}

export async function getBackendWsBaseUrl(): Promise<string> {
  const ep = await ensureLoaded();
  return `${ep.scheme === "https" ? "wss" : "ws"}://${ep.host}:${ep.port}/api`;
}

export function isPrivateHttpHost(host: unknown): boolean {
  const normalized = String(host || "").trim().toLowerCase();
  if (normalized === "localhost" || normalized.endsWith(".local") || normalized.endsWith(".lan")) {
    return true;
  }
  const parts = normalized.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part))) return false;
  const [a, b] = parts;
  return a === 127 || a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

function getPermissionsApi(): PermissionsLike | null {
  try {
    return globalThis.chrome?.permissions ?? null;
  } catch {
    return null;
  }
}

function invokePermission(
  api: PermissionsLike,
  method: "contains" | "request",
  details: chrome.permissions.Permissions,
): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    try {
      api[method](details, (granted) => resolve(Boolean(granted)));
    } catch {
      resolve(false);
    }
  });
}

export async function requestBackendPermission(
  endpoint: BackendEndpoint,
  permissionsApi: PermissionsLike | null = getPermissionsApi(),
): Promise<boolean> {
  // WebExtension match patterns cannot portably scope host permissions by port:
  // Firefox ignores port-qualified patterns. Keep the endpoint itself pinned to
  // its configured port, while requesting the narrowest cross-browser pattern.
  const origin = `${endpoint.scheme}://${endpoint.host}/*`;
  if (!permissionsApi?.contains || !permissionsApi?.request) {
    return endpoint.scheme === "http" && ["127.0.0.1", "localhost"].includes(endpoint.host);
  }
  const details = { origins: [origin] };
  if (await invokePermission(permissionsApi, "contains", details)) return true;
  return invokePermission(permissionsApi, "request", details);
}

export function isValidBackendHost(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const trimmed = value.trim();
  if (trimmed === "" || trimmed === "localhost") return true;
  if (/^(\d{1,3}\.){3}\d{1,3}$/.test(trimmed)) {
    return trimmed.split(".").every((p) => {
      const n = Number(p);
      return n >= 0 && n <= 255;
    });
  }
  if (/^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$/.test(trimmed)) {
    return true;
  }
  return false;
}

export async function updateBackendEndpoint(
  scheme: unknown,
  host: unknown,
  port: unknown,
  options: UpdateBackendEndpointOptions = {},
): Promise<BackendEndpoint> {
  if (scheme !== "http" && scheme !== "https") {
    throw new Error("invalid_backend_scheme");
  }
  if (!isValidBackendPort(port)) {
    throw new Error("端口必须是 1-65535 的整数");
  }
  const hostStr = typeof host === "string" ? host.trim() : "";
  if (hostStr !== "" && !isValidBackendHost(hostStr)) {
    throw new Error("后端地址必须是有效的 IP 地址或主机名");
  }
  const normalizedHost = hostStr || DEFAULT_BACKEND_HOST;
  if (scheme === "http" && !isPrivateHttpHost(normalizedHost)) {
    throw new Error("https_required");
  }
  const endpoint: BackendEndpoint = {
    scheme,
    host: normalizedHost,
    port: coercePort(port),
  };
  const granted = await requestBackendPermission(endpoint, options.permissionsApi);
  if (!granted) throw new Error("backend_permission_denied");
  cached = endpoint;
  initialized = true;
  const storage = getStorageLocal();
  if (typeof storage?.set === "function") {
    await new Promise((resolve) => {
      try {
        storage.set({ [BACKEND_ENDPOINT_STORAGE_KEY]: endpoint }, () => resolve(undefined));
      } catch {
        resolve(undefined);
      }
    });
  }
  for (const cb of subscribers) {
    try {
      cb(endpoint);
    } catch {
      // ignore
    }
  }
  return endpoint;
}

export async function updateBackendPort(value: unknown): Promise<BackendEndpoint> {
  if (!isValidBackendPort(value)) {
    throw new Error("端口必须是 1-65535 的整数");
  }
  const port = coercePort(value);
  const endpoint: BackendEndpoint = {
    scheme: cached.scheme || DEFAULT_BACKEND_SCHEME,
    host: cached.host || DEFAULT_BACKEND_HOST,
    port,
  };
  cached = endpoint;
  initialized = true;
  const storage = getStorageLocal();
  if (typeof storage?.set === "function") {
    await new Promise((resolve) => {
      try {
        storage.set({ [BACKEND_ENDPOINT_STORAGE_KEY]: endpoint }, () => resolve(undefined));
      } catch {
        resolve(undefined);
      }
    });
  }
  // Same context's onChanged does not fire for its own writes; notify
  // local subscribers synchronously.
  for (const cb of subscribers) {
    try {
      cb(endpoint);
    } catch {
      // ignore
    }
  }
  return endpoint;
}

export function onBackendEndpointChange(callback: EndpointSubscriber): () => void {
  subscribers.add(callback);
  installStorageChangeListener();
  void ensureLoaded();
  return () => {
    subscribers.delete(callback);
  };
}

/**
 * Test-only: reset module state so a test can stub a fresh
 * chrome.storage.local without inheriting the previous test's cache.
 */
export function __resetBackendEndpointForTests() {
  cached = { ...DEFAULT_ENDPOINT };
  initialized = false;
  initPromise = null;
  storageListenerInstalled = false;
  subscribers.clear();
}
