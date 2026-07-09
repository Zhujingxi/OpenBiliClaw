/** Device-key exchange and Bearer-authenticated extension HTTP requests. */

import { apiUrl } from "./backend-endpoint.ts";
import {
  clearLegacyCredentials,
  clearSession,
  getDeviceKey,
  loadSession,
  saveSession,
} from "./token-store.ts";

const SESSION_REFRESH_SKEW_SECONDS = 60;

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

interface EnsureSessionOptions {
  force?: boolean;
  fetchImpl?: FetchLike;
}

let refreshInFlight: Promise<string | null> | null = null;

function sessionUsable(expiresAt: number, skewSeconds = 0): boolean {
  return expiresAt > Date.now() / 1000 + skewSeconds;
}

export async function getSessionToken(): Promise<string | null> {
  const session = await loadSession();
  return session && sessionUsable(session.expires_at) ? session.token : null;
}

async function exchangeDeviceKey(fetchImpl: FetchLike): Promise<string | null> {
  const key = await getDeviceKey();
  if (!key) return null;
  let response: Response;
  try {
    response = await fetchImpl(await apiUrl("/auth/extension-token"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
  } catch {
    return null;
  }
  if (!response.ok) {
    await clearSession();
    return null;
  }
  const payload = (await response.json()) as {
    ok?: boolean;
    token?: string;
    expires_at?: number;
  };
  if (!payload.ok || !payload.token || !Number.isFinite(payload.expires_at)) {
    await clearSession();
    return null;
  }
  await saveSession({ token: payload.token, expires_at: Number(payload.expires_at) });
  await clearLegacyCredentials();
  return payload.token;
}

export async function ensureSession(options: EnsureSessionOptions = {}): Promise<string | null> {
  const force = options.force === true;
  const current = await loadSession();
  if (!force && current && sessionUsable(current.expires_at, SESSION_REFRESH_SKEW_SECONDS)) {
    return current.token;
  }
  if (refreshInFlight) return refreshInFlight;
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  refreshInFlight = exchangeDeviceKey(fetchImpl).finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

async function refreshAfterUnauthorized(
  rejectedToken: string | null,
  fetchImpl: FetchLike,
): Promise<string | null> {
  const current = await getSessionToken();
  if (current && current !== rejectedToken) return current;
  return ensureSession({ force: true, fetchImpl });
}

function withBearer(init: RequestInit, token: string | null): RequestInit {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  else headers.delete("Authorization");
  return { ...init, headers };
}

export async function authenticatedFetch(
  url: string | URL,
  init: RequestInit = {},
  fetchImpl: FetchLike = globalThis.fetch.bind(globalThis),
): Promise<Response> {
  const token = await ensureSession({ fetchImpl });
  const first = await fetchImpl(url, withBearer(init, token));
  if (first.status !== 401 || !token) return first;

  const refreshed = await refreshAfterUnauthorized(token, fetchImpl);
  if (!refreshed) return first;
  return fetchImpl(url, withBearer(init, refreshed));
}

export async function autoLogin(): Promise<boolean> {
  return (await ensureSession()) !== null;
}

export { clearSession } from "./token-store.ts";

export function __resetAuthForTests(): void {
  refreshInFlight = null;
}
