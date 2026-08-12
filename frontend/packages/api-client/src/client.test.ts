import { describe, expect, it, vi } from "vitest";
import type { components } from "../generated/schema";
import { ApiClient, ApiError, parseEventEnvelope } from "./client";

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

describe("SSE stream", () => {
  it("parses CRLF and multiline data frames", async () => {
    const body = streamOf(
      'data: {"kind":"job",\r\ndata: "event_id":1,"component_id":"core","status":"ok"}\r\n\r\n',
    );
    const client = new ApiClient(
      "",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(body)),
    );
    const events = [];
    for await (const event of client.stream("/v1/events/stream"))
      events.push(event);
    expect(events).toEqual([
      { kind: "job", event_id: 1, component_id: "core", status: "ok" },
    ]);
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
