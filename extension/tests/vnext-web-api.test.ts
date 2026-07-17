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
