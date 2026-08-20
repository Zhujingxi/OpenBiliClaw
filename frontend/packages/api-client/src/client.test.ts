import { describe, expect, it, vi } from "vitest";
import type { components } from "../generated/schema";
import {
  ApiClient,
  ApiError,
  deviceIdentity,
  parseAssistantLifecycleEvent,
  parseEventEnvelope,
  uuid,
} from "./client";

const acceptsGeneratedRequest = (client: ApiClient): void => {
  // @ts-expect-error generated search operation requires query parameters
  void client.request({
    path: "/v1/content/search",
    method: "get",
    validate: (_value): _value is components["schemas"]["SearchResponse"] =>
      true,
  });
  // @ts-expect-error generated detail operation requires query parameters
  void client.request({
    path: "/v1/content/detail",
    method: "get",
    validate: (_value): _value is components["schemas"]["ContentResponse"] =>
      true,
  });
};
void acceptsGeneratedRequest;

describe("ApiClient", () => {
  it("binds requests to generated operations and validates JSON", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(
          '{"health":{"checked_at":"2030-01-01T00:00:00Z","component_id":"core","jobs":[],"status":"healthy"}}',
        ),
      );
    const client = new ApiClient("https://api.example.test", fetcher);
    await expect(
      client.request({
        path: "/v1/runtime/health",
        method: "get",
        validate: isHealth,
      }),
    ).resolves.toMatchObject({ health: { component_id: "core" } });
    expect(fetcher).toHaveBeenCalledWith(
      "https://api.example.test/v1/runtime/health",
      {
        method: "GET",
      },
    );
  });

  it("keeps a valid receiver when the injected fetch requires one", async () => {
    // Chrome 151+ native fetch throws TypeError("Illegal invocation")
    // when invoked detached from Window; the client must not lose the
    // receiver when storing the fetcher.
    const fetcher = vi.fn<typeof fetch>().mockImplementation(function (
      this: unknown,
    ) {
      if (this !== globalThis) {
        return Promise.reject(new TypeError("Illegal invocation"));
      }
      return Promise.resolve(
        new Response(
          '{"health":{"checked_at":"2030-01-01T00:00:00Z","component_id":"core","jobs":[],"status":"healthy"}}',
        ),
      );
    });
    const client = new ApiClient("https://api.example.test", fetcher);
    await expect(
      client.request({
        path: "/v1/runtime/health",
        method: "get",
        validate: isHealth,
      }),
    ).resolves.toMatchObject({ health: { component_id: "core" } });
  });

  it("serializes generated path and query parameters", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(
        async () =>
          new Response(
            '{"content":{"ref":{"canonical_url":"https://example.test","content_kind":{"value":"video"},"provider_content_id":"1","provider_id":{"value":"demo"}},"schema_version":1,"payload":{}},"items":[]}',
          ),
      );
    const client = new ApiClient("https://api.example.test", fetcher);
    const reference = JSON.stringify({
      canonical_url: "https://example.test/1",
    });
    await client.request({
      path: "/v1/content/detail",
      method: "get",
      query: { reference },
      validate: (value): value is components["schemas"]["ContentResponse"] =>
        typeof value === "object" && value !== null && "content" in value,
    });
    expect(fetcher).toHaveBeenCalledWith(
      `https://api.example.test/v1/content/detail?${new URLSearchParams({ reference })}`,
      { method: "GET" },
    );

    await client.request({
      path: "/v1/content/search",
      method: "get",
      query: { provider_id: "demo", q: "space query", limit: 5 },
      validate: (value): value is components["schemas"]["SearchResponse"] =>
        typeof value === "object" && value !== null,
    });
    expect(fetcher).toHaveBeenLastCalledWith(
      "https://api.example.test/v1/content/search?provider_id=demo&q=space+query&limit=5",
      { method: "GET" },
    );
  });

  it("persists one device identity and attaches the host mutation headers", async () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    };
    const first = deviceIdentity(storage, () => "device-123");
    expect(deviceIdentity(storage, () => "other")).toBe(first);
    expect(deviceIdentity(storage)).toBe(first);
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(
          '{"availability_refreshed":true,"recoverable":false,"status":{"provider_id":"demo","account_id":null,"state":"connected"}}',
        ),
      );
    await new ApiClient("", fetcher, first).request({
      path: "/v1/sources/connect",
      method: "post",
      body: {
        provider_id: "demo",
        method_id: "manual",
        idempotency_key: "connect:1",
        permissions: ["read_public"],
      },
      validate: (
        value,
      ): value is components["schemas"]["SourceMutationResponse"] =>
        isRecord(value) && "status" in value,
    });
    expect(fetcher).toHaveBeenCalledWith("/v1/sources/connect", {
      method: "POST",
      body: JSON.stringify({
        provider_id: "demo",
        method_id: "manual",
        idempotency_key: "connect:1",
        permissions: ["read_public"],
      }),
      headers: {
        "content-type": "application/json",
        "X-Device-ID": "device-123",
        "X-CSRF-Token": "device-123",
      },
    });
  });

  it("posts generated feedback bodies with mutation headers", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(
          '{"result":{"feedback_id":"feedback_11111111111111111111111111111111","observation_id":"obs_11111111111111111111111111111111","inserted":true}}',
        ),
      );
    await new ApiClient("", fetcher, "device-123").request({
      path: "/v1/feedback",
      method: "post",
      body: {
        idempotency_key: "feedback:shown-1:liked",
        shown_id: "shown-1",
        content_ref: {
          provider_id: { value: "demo" },
          content_kind: { value: "video" },
          provider_content_id: "one",
          canonical_url: "https://example.test/one",
        },
        kind: "liked",
        exposed: false,
      },
      validate: (value): value is components["schemas"]["FeedbackResponse"] =>
        isRecord(value) && "result" in value,
    });
    expect(fetcher).toHaveBeenCalledWith("/v1/feedback", {
      method: "POST",
      body: JSON.stringify({
        idempotency_key: "feedback:shown-1:liked",
        shown_id: "shown-1",
        content_ref: {
          provider_id: { value: "demo" },
          content_kind: { value: "video" },
          provider_content_id: "one",
          canonical_url: "https://example.test/one",
        },
        kind: "liked",
        exposed: false,
      }),
      headers: {
        "content-type": "application/json",
        "X-Device-ID": "device-123",
        "X-CSRF-Token": "device-123",
      },
    });
  });

  it("fails closed when a mutation has no device identity", async () => {
    const client = new ApiClient("", vi.fn<typeof fetch>());
    await expect(
      client.request({
        path: "/v1/sources/connect",
        method: "post",
        body: {
          provider_id: "demo",
          method_id: "manual",
          idempotency_key: "connect:1",
          permissions: ["read_public"],
        },
        validate: (
          value,
        ): value is components["schemas"]["SourceMutationResponse"] =>
          isRecord(value),
      }),
    ).rejects.toMatchObject({ kind: "invalid-response" });
  });

  it("rejects unknown response bodies", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response('{"wrong":true}'));
    await expect(
      new ApiClient("", fetcher).request({
        path: "/v1/runtime/health",
        method: "get",
        validate: isHealth,
      }),
    ).rejects.toMatchObject({ kind: "invalid-response" });
  });

  it("preserves cancellation and rejects unresolved path templates", async () => {
    const cancelled = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new DOMException("Aborted", "AbortError"));
    await expect(
      new ApiClient("", cancelled).request({
        path: "/v1/runtime/health",
        method: "get",
        validate: isHealth,
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
    const client = new ApiClient("", vi.fn<typeof fetch>());
    await expect(
      client.request({
        path: "/v1/profiles/{profile_id}",
        method: "get",
        pathParams: { profile_id: undefined as unknown as string },
        validate: (
          _value,
        ): _value is components["schemas"]["ProfileResponse"] => true,
      }),
    ).rejects.toMatchObject({ kind: "invalid-response" });
  });

  it("normalizes network, HTTP, and malformed JSON failures", async () => {
    const failed = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new Error("offline"));
    await expect(
      new ApiClient("", failed).request({
        path: "/v1/runtime/health",
        method: "get",
        validate: isHealth,
      }),
    ).rejects.toEqual(new ApiError("network", "Network request failed"));
    const denied = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("secret", { status: 401 }));
    await expect(
      new ApiClient("", denied).request({
        path: "/v1/runtime/health",
        method: "get",
        validate: isHealth,
      }),
    ).rejects.toMatchObject({ kind: "http", status: 401 });
    const typed = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: "unauthorized", message: "source is not connected" },
        }),
        { status: 401, headers: { "content-type": "application/json" } },
      ),
    );
    await expect(
      new ApiClient("", typed).request({
        path: "/v1/runtime/health",
        method: "get",
        validate: isHealth,
      }),
    ).rejects.toMatchObject({
      kind: "http",
      status: 401,
      code: "unauthorized",
      message: "source is not connected",
    });
    const malformed = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("{"));
    await expect(
      new ApiClient("", malformed).request({
        path: "/v1/runtime/health",
        method: "get",
        validate: isHealth,
      }),
    ).rejects.toMatchObject({ kind: "invalid-response" });
  });
});

describe("uuid", () => {
  it("generates RFC 4122 v4 identifiers without a secure context", () => {
    const cryptoSpy = vi
      .spyOn(globalThis, "crypto", "get")
      .mockReturnValue(undefined as unknown as Crypto);
    try {
      expect(uuid()).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      );
    } finally {
      cryptoSpy.mockRestore();
    }
    expect(uuid()).not.toBe(uuid());
  });
});

describe("SSE stream", () => {
  it("posts Assistant turns and runtime-validates every lifecycle event", async () => {
    const meter = {
      approximate_usage_percent: 25,
      context_window_tokens: 1000,
      estimated_input_tokens: 250,
      excluded_oldest_turns: 0,
    };
    const events = [
      { kind: "turn_started", context_meter: meter },
      { kind: "reasoning_started" },
      { kind: "reasoning_delta", delta: "Because" },
      { kind: "reasoning_finished" },
      { kind: "tool_started", name: "Search" },
      {
        kind: "tool_finished",
        name: "Search",
        status: "succeeded",
        summary: "Found relevant items",
      },
      { kind: "response_delta", delta: "Answer" },
      {
        kind: "turn_finished",
        context_meter: meter,
        output: { kind: "message", text: "Answer" },
        usage: { input_tokens: 10, output_tokens: 2, request_count: 1 },
      },
      { kind: "error", code: "temporary_failure", message: "Try later" },
    ];
    const body = streamOf(
      events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
    );
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body));
    const request = {
      conversation_id: "conv_11111111111111111111111111111111",
      locale: "zh-CN",
      text: "hello",
    };
    const result = await collect(
      new ApiClient("", fetcher, "device-123").assistantStream(
        request,
        { "X-Device-ID": "device-123" },
        undefined,
      ),
    );
    expect(result.map((event) => event.kind)).toEqual(
      events.map((event) => event.kind),
    );
    expect(fetcher).toHaveBeenCalledWith("/v1/assistant/turns/stream", {
      method: "POST",
      body: JSON.stringify(request),
      headers: {
        accept: "text/event-stream",
        "content-type": "application/json",
        "X-Device-ID": "device-123",
        "X-CSRF-Token": "device-123",
      },
    });
  });

  it("rejects invalid Assistant lifecycle fields", async () => {
    expect(() =>
      parseAssistantLifecycleEvent(
        '{"kind":"tool_finished","name":"Search","status":"ok","summary":"safe"}',
      ),
    ).toThrow(ApiError);
    const client = new ApiClient(
      "",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          new Response(streamOf('data: {"kind":"response_delta"}\n\n')),
        ),
      "device-123",
    );
    await expect(
      collect(
        client.assistantStream(
          { conversation_id: "conv", locale: "en", text: "hello" },
          { "X-Device-ID": "device-123" },
        ),
      ),
    ).rejects.toMatchObject({ kind: "invalid-response" });
  });

  it("passes the replay cursor and parses CRLF and multiline data frames", async () => {
    const body = streamOf(
      'data: {"kind":"job",\r\ndata: "event_id":1,"component_id":"core","status":"ok"}\r\n\r\n',
    );
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body));
    const client = new ApiClient("", fetcher);
    const stream = client.stream("/v1/events/stream", 7);
    await expect(stream.next()).resolves.toEqual({
      done: false,
      value: { kind: "job", event_id: 1, component_id: "core", status: "ok" },
    });
    await stream.return(undefined);
    expect(fetcher).toHaveBeenCalledWith("/v1/events/stream?after=7", {});
  });

  it("exposes the server retry hint when a stream disconnects", async () => {
    const body = streamOf(
      "retry: 3000\n\ndata: " +
        '{"kind":"job","event_id":1,"component_id":"core","status":"ok"}\n\n',
    );
    const client = new ApiClient(
      "",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(body)),
    );
    const stream = client.stream("/v1/events/stream");
    await expect(stream.next()).resolves.toMatchObject({ done: false });
    await expect(stream.next()).rejects.toMatchObject({
      kind: "network",
      retryMilliseconds: 3000,
    });
  });

  it.each([
    ["http", new Response("", { status: 503 })],
    ["invalid-response", new Response(null)],
    ["invalid-response", new Response(streamOf('data: {"kind":"evil"}\n\n'))],
    ["invalid-response", new Response(streamOf("data: {"))],
  ] as const)("normalizes %s stream failures", async (kind, response) => {
    const client = new ApiClient(
      "",
      vi.fn<typeof fetch>().mockResolvedValue(response),
    );
    await expect(
      collect(client.stream("/v1/events/stream")),
    ).rejects.toMatchObject({ kind });
  });

  it("normalizes connection and mid-stream read failures", async () => {
    const connectFailure = new ApiClient(
      "",
      vi.fn<typeof fetch>().mockRejectedValue(new Error("offline")),
    );
    await expect(
      collect(connectFailure.stream("/v1/events/stream")),
    ).rejects.toMatchObject({
      kind: "network",
    });
    const broken = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.error(new Error("socket reset"));
      },
    });
    const client = new ApiClient(
      "",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(broken)),
    );
    await expect(
      collect(client.stream("/v1/events/stream")),
    ).rejects.toMatchObject({
      kind: "network",
    });
  });
});

it("validates event payloads", () => {
  expect(
    parseEventEnvelope(
      '{"kind":"job","event_id":1,"component_id":"core","status":"ok"}',
    ),
  ).toMatchObject({ kind: "job", event_id: 1 });
  expect(() => parseEventEnvelope("not-json")).toThrow(ApiError);
});

function isHealth(
  value: unknown,
): value is components["schemas"]["RuntimeResponse"] {
  return (
    isRecord(value) &&
    isRecord(value.health) &&
    typeof value.health.component_id === "string" &&
    typeof value.health.checked_at === "string" &&
    Array.isArray(value.health.jobs) &&
    value.health.jobs.length === 0 &&
    value.health.status === "healthy"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function streamOf(value: string): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(value));
      controller.close();
    },
  });
}

async function collect<T>(values: AsyncIterable<T>): Promise<T[]> {
  const result: T[] = [];
  for await (const value of values) result.push(value);
  return result;
}
