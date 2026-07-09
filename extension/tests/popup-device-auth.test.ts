import assert from "node:assert/strict";
import test from "node:test";

import { __resetBackendEndpointForTests } from "../popup/popup-backend-config.js";
import {
  __resetPopupDeviceAuthForTests,
  pairDeviceKey,
  popupAuthenticatedFetch,
  readPopupSessionToken,
} from "../popup/popup-device-auth.js";

function storageHarness(initial: Record<string, unknown> = {}) {
  const values = { ...initial };
  const writes: Array<Record<string, unknown>> = [];
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    storage: { local: {
      get(keys: string | string[], callback: (items: Record<string, unknown>) => void) {
        const selected = Array.isArray(keys) ? keys : [keys];
        callback(Object.fromEntries(selected.filter((key) => key in values).map((key) => [key, values[key]])));
      },
      set(items: Record<string, unknown>, callback: () => void) {
        writes.push(items);
        Object.assign(values, items);
        callback();
      },
      remove(keys: string | string[], callback: () => void) {
        for (const key of Array.isArray(keys) ? keys : [keys]) delete values[key];
        callback();
      },
    } },
  };
  __resetPopupDeviceAuthForTests();
  __resetBackendEndpointForTests();
  return { values, writes, restore() {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    __resetPopupDeviceAuthForTests();
    __resetBackendEndpointForTests();
  } };
}

test("pairDeviceKey stores the key and a structured short session, then removes legacy credentials", async () => {
  const storage = storageHarness({ obc_auth_password: "pw", obc_auth_token: "old" });
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  try {
    await pairDeviceKey(" obc_ext_device.secret ", {
      getBaseUrl: async () => "https://backend.example/api",
      fetchImpl: async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return Response.json({ ok: true, token: "short", expires_at: 2_000_000_000 });
      },
    });
    assert.equal(storage.values.obc_extension_device_key, "obc_ext_device.secret");
    assert.deepEqual(storage.values.obc_auth_session, {
      token: "short", expires_at: 2_000_000_000,
    });
    assert.equal(storage.writes.some((write) => (
      "obc_extension_device_key" in write && "obc_auth_session" in write
    )), true);
    assert.equal("obc_auth_password" in storage.values, false);
    assert.equal("obc_auth_token" in storage.values, false);
    assert.equal(calls[0].url, "https://backend.example/api/auth/extension-token");
    assert.equal(JSON.parse(String(calls[0].init?.body)).key, "obc_ext_device.secret");
    assert.equal(await readPopupSessionToken(), "short");
  } finally {
    storage.restore();
  }
});

test("pairDeviceKey preserves stable backend error codes and removes a rejected key", async () => {
  const storage = storageHarness();
  try {
    await assert.rejects(
      pairDeviceKey("bad", {
        getBaseUrl: async () => "https://backend.example/api",
        fetchImpl: async () => Response.json(
          { error: "extension_access_disabled" }, { status: 403 },
        ),
      }),
      /extension_access_disabled/,
    );
    assert.equal("obc_extension_device_key" in storage.values, false);
  } finally {
    storage.restore();
  }
});

test("popupAuthenticatedFetch uses Bearer without token query and retries one 401", async () => {
  const storage = storageHarness({
    obc_extension_device_key: "device-key",
    obc_auth_session: { token: "session-1", expires_at: 2_000_000_000 },
  });
  const protectedCalls: Array<{ url: string; token: string | null }> = [];
  let exchanges = 0;
  try {
    const response = await popupAuthenticatedFetch("https://backend/api/config", {}, async (url, init) => {
      if (String(url).endsWith("/auth/extension-token")) {
        exchanges += 1;
        return Response.json({ ok: true, token: "session-2", expires_at: 2_000_000_000 });
      }
      protectedCalls.push({
        url: String(url), token: new Headers(init?.headers).get("authorization"),
      });
      return new Response("", { status: protectedCalls.length === 1 ? 401 : 200 });
    });
    assert.equal(response.status, 200);
    assert.equal(exchanges, 1);
    assert.deepEqual(protectedCalls.map((call) => call.token), [
      "Bearer session-1", "Bearer session-2",
    ]);
    assert.equal(protectedCalls.some((call) => call.url.includes("token=")), false);
  } finally {
    storage.restore();
  }
});
