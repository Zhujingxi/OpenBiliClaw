import assert from "node:assert/strict";
import test from "node:test";

import { __resetBackendEndpointForTests } from "../src/shared/backend-endpoint.ts";
import {
  __resetAuthForTests,
  authenticatedFetch,
  ensureSession,
  getSessionToken,
} from "../src/shared/auth.ts";
import {
  __resetTokenStoreForTests,
  clearLegacyCredentials,
  loadSession,
  saveSession,
} from "../src/shared/token-store.ts";

function installStorage(initial: Record<string, unknown> = {}) {
  const values = { ...initial };
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    storage: {
      local: {
        get(keys: string | string[], callback: (items: Record<string, unknown>) => void) {
          const selected = Array.isArray(keys) ? keys : [keys];
          callback(Object.fromEntries(selected.filter((key) => key in values).map((key) => [key, values[key]])));
        },
        set(items: Record<string, unknown>, callback: () => void) {
          Object.assign(values, items);
          callback();
        },
        remove(keys: string | string[], callback: () => void) {
          for (const key of Array.isArray(keys) ? keys : [keys]) delete values[key];
          callback();
        },
      },
    },
  };
  __resetTokenStoreForTests();
  __resetAuthForTests();
  __resetBackendEndpointForTests();
  return {
    values,
    restore() {
      (globalThis as { chrome?: unknown }).chrome = originalChrome;
      __resetTokenStoreForTests();
      __resetAuthForTests();
      __resetBackendEndpointForTests();
    },
  };
}

test("structured sessions round-trip and legacy credentials are removed", async () => {
  const storage = installStorage({ obc_auth_password: "pw", obc_auth_token: "legacy" });
  try {
    await saveSession({ token: "session", expires_at: 2_000_000_000 });
    assert.deepEqual(await loadSession(), { token: "session", expires_at: 2_000_000_000 });
    await clearLegacyCredentials();
    assert.equal("obc_auth_password" in storage.values, false);
    assert.equal("obc_auth_token" in storage.values, false);
  } finally {
    storage.restore();
  }
});

test("ensureSession refreshes within 60 seconds and returns null without a device key", async () => {
  const storage = installStorage();
  try {
    assert.equal(await ensureSession({ fetchImpl: async () => { throw new Error("unused"); } }), null);
    storage.values.obc_extension_device_key = "device-key";
    storage.values.obc_auth_session = { token: "expiring", expires_at: Date.now() / 1000 + 30 };
    __resetTokenStoreForTests();
    let calls = 0;
    const token = await ensureSession({ fetchImpl: async () => {
      calls += 1;
      return Response.json({ ok: true, token: "fresh", expires_at: Date.now() / 1000 + 3600 });
    } });
    assert.equal(token, "fresh");
    assert.equal(calls, 1);
    assert.equal(await getSessionToken(), "fresh");
  } finally {
    storage.restore();
  }
});

test("invalid exchange clears the short session", async () => {
  const storage = installStorage({
    obc_extension_device_key: "bad-key",
    obc_auth_session: { token: "old", expires_at: 2_000_000_000 },
  });
  try {
    assert.equal(await ensureSession({ force: true, fetchImpl: async () => new Response("", { status: 401 }) }), null);
    assert.equal("obc_auth_session" in storage.values, false);
  } finally {
    storage.restore();
  }
});

test("authenticatedFetch uses a Bearer header and no token query", async () => {
  const storage = installStorage({
    obc_auth_session: { token: "session-1", expires_at: 2_000_000_000 },
  });
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  try {
    const response = await authenticatedFetch("https://backend/api/runtime-status", {}, async (url, init) => {
      calls.push({ url: String(url), init });
      return Response.json({ ok: true });
    });
    assert.equal(response.status, 200);
    assert.equal(calls[0].url.includes("token="), false);
    assert.equal(new Headers(calls[0].init?.headers).get("authorization"), "Bearer session-1");
  } finally {
    storage.restore();
  }
});

test("concurrent 401 responses share one refresh and each replay once", async () => {
  const storage = installStorage({
    obc_extension_device_key: "device-key",
    obc_auth_session: { token: "session-1", expires_at: 2_000_000_000 },
  });
  let exchanges = 0;
  let protectedCalls = 0;
  const fetchImpl = async (url: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    if (String(url).endsWith("/auth/extension-token")) {
      exchanges += 1;
      await new Promise((resolve) => setTimeout(resolve, 10));
      return Response.json({ ok: true, token: "session-2", expires_at: 2_000_000_000 });
    }
    protectedCalls += 1;
    const token = new Headers(init?.headers).get("authorization");
    return new Response("", { status: token === "Bearer session-2" ? 200 : 401 });
  };
  try {
    const [first, second] = await Promise.all([
      authenticatedFetch("https://backend/api/a", {}, fetchImpl),
      authenticatedFetch("https://backend/api/b", {}, fetchImpl),
    ]);
    assert.equal(first.status, 200);
    assert.equal(second.status, 200);
    assert.equal(exchanges, 1);
    assert.equal(protectedCalls, 4);
  } finally {
    storage.restore();
  }
});

test("authenticatedFetch never retries a second 401", async () => {
  const storage = installStorage({
    obc_extension_device_key: "device-key",
    obc_auth_session: { token: "session-1", expires_at: 2_000_000_000 },
  });
  let protectedCalls = 0;
  try {
    const response = await authenticatedFetch("https://backend/api/config", {}, async (url) => {
      if (String(url).endsWith("/auth/extension-token")) {
        return Response.json({ ok: true, token: "session-2", expires_at: 2_000_000_000 });
      }
      protectedCalls += 1;
      return new Response("", { status: 401 });
    });
    assert.equal(response.status, 401);
    assert.equal(protectedCalls, 2);
  } finally {
    storage.restore();
  }
});
