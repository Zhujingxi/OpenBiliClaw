import assert from "node:assert/strict";
import test from "node:test";

import {
  completionRequestBody,
  deliverTaskCompletion,
  isTerminalCompletionError,
  type PendingTaskCompletion,
} from "../src/background/task-completion.ts";
import { createDurableOutbox } from "../src/background/durable-outbox.ts";

test("success and failure completion variants preserve one exact idempotent body", () => {
  const success: PendingTaskCompletion = {
    id: "task", leaseToken: "lease", outcome: { result: { operation: "search", items: [] } },
  };
  const failure: PendingTaskCompletion = {
    id: "task", leaseToken: "lease", outcome: { failure: { code: "execution_failed", error_type: "Error" } },
  };
  assert.deepEqual(completionRequestBody(success), {
    lease_token: "lease", result: { operation: "search", items: [] },
  });
  assert.deepEqual(completionRequestBody(failure), {
    lease_token: "lease", failure: { code: "execution_failed", error_type: "Error" },
  });
});

test("only permanent task-state responses are terminal", () => {
  assert.equal(isTerminalCompletionError({ status: 404 }), true);
  assert.equal(isTerminalCompletionError({ status: 409 }), true);
  assert.equal(isTerminalCompletionError({ status: 422 }), true);
  assert.equal(isTerminalCompletionError({ status: 503 }), false);
  assert.equal(isTerminalCompletionError(new TypeError("network")), false);
});

test("a terminal stale completion is dead-lettered and later FIFO records still drain", async () => {
  const data: Record<string, unknown> = {};
  const outbox = createDurableOutbox<PendingTaskCompletion>({
    storage: {
      async get(key) { return Object.hasOwn(data, key) ? { [key]: structuredClone(data[key]) } : {}; },
      async set(items) { Object.assign(data, structuredClone(items)); },
    },
    storageKey: "completions",
  });
  const first: PendingTaskCompletion = {
    id: "stale", leaseToken: "old", outcome: { failure: { code: "execution_failed", error_type: "Error" } },
  };
  const second: PendingTaskCompletion = {
    id: "current", leaseToken: "new", outcome: { result: { operation: "search", items: [] } },
  };
  await outbox.enqueue(first);
  await outbox.enqueue(second);
  const posted: unknown[] = [];
  const deadLetters: unknown[] = [];
  await outbox.flush((completion) => deliverTaskCompletion(
    completion,
    async (body) => {
      posted.push(body);
      if (completion.id === "stale") throw { status: 409 };
    },
    async (record, status) => { deadLetters.push({ record, status }); },
  ));
  assert.equal(await outbox.size(), 0);
  assert.equal(posted.length, 2);
  assert.deepEqual(deadLetters, [{ record: first, status: 409 }]);
});

test("failure completions survive a service-worker restart before delivery", async () => {
  const data: Record<string, unknown> = {};
  const storage = {
    async get(key: string) { return Object.hasOwn(data, key) ? { [key]: structuredClone(data[key]) } : {}; },
    async set(items: Record<string, unknown>) { Object.assign(data, structuredClone(items)); },
  };
  const failure: PendingTaskCompletion = {
    id: "failed-task",
    leaseToken: "same-lease",
    outcome: { failure: { code: "execution_failed", error_type: "TypeError" } },
  };
  await createDurableOutbox<PendingTaskCompletion>({ storage, storageKey: "completions" }).enqueue(failure);
  const restarted = createDurableOutbox<PendingTaskCompletion>({ storage, storageKey: "completions" });
  assert.deepEqual(await restarted.snapshot(), [failure]);
});
