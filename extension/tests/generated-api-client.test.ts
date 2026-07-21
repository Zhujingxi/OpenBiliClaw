import assert from "node:assert/strict";
import test from "node:test";

import {
  API_OPERATIONS,
  buildOperationUrl,
  createApiClient,
  parseSseFrame,
} from "../src/shared/api-client.ts";

test("generated operation metadata builds encoded v1 URLs", () => {
  assert.equal(
    buildOperationUrl(
      "http://127.0.0.1:8420",
      API_OPERATIONS.v1_source_tasks_complete,
      { task_id: "a/b" },
    ),
    "http://127.0.0.1:8420/api/v1/source-tasks/a%2Fb/complete",
  );
});

test("generated client applies bearer auth and parses JSON", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8420",
    getAccessToken: () => "session",
    fetchImpl: async (url, init) => {
      calls.push({ url: String(url), init });
      return new Response(JSON.stringify({ ready: true, version: "test" }), {
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  assert.deepEqual(await client.request("v1_system_readiness"), {
    ready: true,
    version: "test",
  });
  assert.equal(calls[0]?.url, "http://127.0.0.1:8420/api/v1/system/readiness");
  assert.equal(new Headers(calls[0]?.init?.headers).get("Authorization"), "Bearer session");
});

test("generated SSE parser keeps typed event names and JSON data", () => {
  assert.deepEqual(parseSseFrame('event: progress\ndata: {"progress":0.5}'), {
    event: "progress",
    data: { progress: 0.5 },
  });
});
