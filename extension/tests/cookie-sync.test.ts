import test from "node:test";
import assert from "node:assert/strict";

type Cookie = {
  name: string;
  value: string;
  domain?: string;
};

type CookieChangeListener = (changeInfo: {
  cookie: { name: string; domain: string };
  removed: boolean;
}) => void;

let importCounter = 0;

async function importCookieSync() {
  importCounter += 1;
  return import(`../src/background/cookie-sync.ts?case=${importCounter}`);
}

function installChromeMock(cookies: Cookie[]) {
  const listeners: CookieChangeListener[] = [];
  const alarms: Array<{ name: string; info: Record<string, number> }> = [];

  globalThis.chrome = {
    cookies: {
      getAll: async () => cookies,
      onChanged: {
        addListener: (listener: CookieChangeListener) => {
          listeners.push(listener);
        },
      },
    },
    alarms: {
      create: (name: string, info: Record<string, number>) => {
        alarms.push({ name, info });
      },
    },
  } as unknown as typeof chrome;

  return { listeners, alarms };
}

test("startCookieSync retries quickly when the backend is not ready", async () => {
  const { startCookieSync } = await importCookieSync();
  const { alarms } = installChromeMock([
    { name: "SESSDATA", value: "sess" },
    { name: "bili_jct", value: "csrf" },
    { name: "DedeUserID", value: "42" },
  ]);
  globalThis.fetch = async () => {
    throw new Error("backend down");
  };

  startCookieSync();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(alarms.at(-1), {
    name: "openbiliclaw-cookie-sync",
    info: { delayInMinutes: 1, periodInMinutes: 1 },
  });
});

test("startCookieSync registers cookie listener only once", async () => {
  const { startCookieSync } = await importCookieSync();
  const { listeners } = installChromeMock([
    { name: "SESSDATA", value: "sess" },
    { name: "bili_jct", value: "csrf" },
    { name: "DedeUserID", value: "42" },
  ]);
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ ok: true, authenticated: true }), { status: 200 });

  startCookieSync();
  startCookieSync();

  assert.equal(listeners.length, 1);
});

test("cookie sync runtime event posts the current bilibili cookie immediately", async () => {
  const { handleCookieSyncRuntimeEvent } = await importCookieSync();
  installChromeMock([
    { name: "SESSDATA", value: "sess" },
    { name: "bili_jct", value: "csrf" },
    { name: "DedeUserID", value: "42" },
  ]);
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({
      url: String(url),
      body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    });
    return new Response(JSON.stringify({ ok: true, authenticated: true }), { status: 200 });
  };

  const handled = handleCookieSyncRuntimeEvent({
    type: "bilibili_cookie_sync_requested",
    reason: "missing_cookie",
  });
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(handled, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/bilibili/cookie");
  assert.deepEqual(calls[0].body, {
    cookie: "SESSDATA=sess; bili_jct=csrf; DedeUserID=42",
    source: "runtime-stream-request",
    validate_with_bilibili: true,
  });
});
