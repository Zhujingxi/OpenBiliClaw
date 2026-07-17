import assert from "node:assert/strict";
import test from "node:test";

test("web client sends cookies and CSRF on unsafe generated operations", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const originalFetch = globalThis.fetch;
  const originalWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = { dispatchEvent() {} };
  globalThis.fetch = (async (input, init = {}) => {
    calls.push({ url: String(input), init });
    return Response.json({ onboarding_complete: false });
  }) as typeof fetch;
  try {
    const { request } = await import(
      "../../src/openbiliclaw/web/js/vnext-api.js?csrf-contract"
    );
    await request("v1_settings_patch", { body: { feed: { low_watermark: 10 } } });
    assert.equal(calls[0]?.url, "/api/v1/settings");
    assert.equal(calls[0]?.init.credentials, "same-origin");
    assert.equal(new Headers(calls[0]?.init.headers).get("X-OBC-Auth"), "1");
  } finally {
    globalThis.fetch = originalFetch;
    (globalThis as { window?: unknown }).window = originalWindow;
  }
});

test("web login sends only the password body with same-origin CSRF protection", async () => {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const originalFetch = globalThis.fetch;
  const originalWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = { dispatchEvent() {} };
  globalThis.fetch = (async (input, init = {}) => {
    calls.push({ url: String(input), init });
    return Response.json({ authenticated: true });
  }) as typeof fetch;
  try {
    const { request } = await import(
      "../../src/openbiliclaw/web/js/vnext-api.js?login-contract"
    );
    await request("v1_auth_login", { body: { password: "correct horse" } });
    assert.equal(calls.length, 1);
    assert.equal(calls[0]?.url, "/api/v1/auth/login");
    assert.equal(calls[0]?.init.method, "POST");
    assert.equal(calls[0]?.init.credentials, "same-origin");
    assert.equal(new Headers(calls[0]?.init.headers).get("X-OBC-Auth"), "1");
    assert.deepEqual(JSON.parse(String(calls[0]?.init.body)), {
      password: "correct horse",
    });
  } finally {
    globalThis.fetch = originalFetch;
    (globalThis as { window?: unknown }).window = originalWindow;
  }
});

test("web 401 handling signals auth once without replaying the failed request", async () => {
  const events: string[] = [];
  let calls = 0;
  const originalFetch = globalThis.fetch;
  const originalWindow = (globalThis as { window?: unknown }).window;
  const originalCustomEvent = (globalThis as { CustomEvent?: unknown }).CustomEvent;
  (globalThis as { CustomEvent?: unknown }).CustomEvent = class {
    type: string;
    constructor(type: string) { this.type = type; }
  };
  (globalThis as { window?: unknown }).window = {
    dispatchEvent(event: { type?: string }) { events.push(String(event.type)); },
  };
  globalThis.fetch = (async () => {
    calls += 1;
    return Response.json(
      { error: { code: "auth_required", message: "login required" } },
      { status: 401 },
    );
  }) as typeof fetch;
  try {
    const { request } = await import(
      "../../src/openbiliclaw/web/js/vnext-api.js?bounded-401-contract"
    );
    await assert.rejects(request("v1_feed_list"));
    assert.equal(calls, 1);
    assert.deepEqual(events, ["obc:auth-required"]);
  } finally {
    globalThis.fetch = originalFetch;
    (globalThis as { window?: unknown }).window = originalWindow;
    (globalThis as { CustomEvent?: unknown }).CustomEvent = originalCustomEvent;
  }
});

test("web SSE uses authenticated fetch and keeps typed frames", async () => {
  const calls: RequestInit[] = [];
  const originalFetch = globalThis.fetch;
  const originalWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = { dispatchEvent() {} };
  globalThis.fetch = (async (_input, init = {}) => {
    calls.push(init);
    return new Response('event: delta\ndata: {"text":"hello"}\n\n', {
      headers: { "Content-Type": "text/event-stream" },
    });
  }) as typeof fetch;
  try {
    const { readSse } = await import(
      "../../src/openbiliclaw/web/js/vnext-api.js?sse-contract"
    );
    const events: unknown[] = [];
    await readSse(
      "v1_chat_stream",
      { body: { conversation_id: crypto.randomUUID(), message: "hi" } },
      (event: unknown) => events.push(event),
    );
    assert.equal(calls[0]?.credentials, "same-origin");
    assert.equal(new Headers(calls[0]?.headers).get("X-OBC-Auth"), "1");
    assert.deepEqual(events, [{ event: "delta", data: { text: "hello" } }]);
  } finally {
    globalThis.fetch = originalFetch;
    (globalThis as { window?: unknown }).window = originalWindow;
  }
});

test("interaction requests reject so callers cannot commit success UI after a backend failure", async () => {
  const originalFetch = globalThis.fetch;
  const originalWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = { dispatchEvent() {} };
  globalThis.fetch = (async () => Response.json(
    { error: { code: "interaction_rejected", message: "rejected" } },
    { status: 500 },
  )) as typeof fetch;
  try {
    const { recordInteraction } = await import(
      "../../src/openbiliclaw/web/js/vnext-api.js?interaction-failure-contract"
    );
    await assert.rejects(recordInteraction("content-1", "positive", "mobile_web"));
  } finally {
    globalThis.fetch = originalFetch;
    (globalThis as { window?: unknown }).window = originalWindow;
  }
});
