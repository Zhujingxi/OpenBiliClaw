import assert from "node:assert/strict";
import test from "node:test";

import {
  deliverActivityEvent,
  isTerminalActivityError,
} from "../src/background/activity-delivery.ts";
import { createDurableOutbox, type OutboxStorage } from "../src/background/durable-outbox.ts";
import type { ActivityEvent } from "../src/shared/api-client.ts";

function memoryStorage(): OutboxStorage {
  const data: Record<string, unknown> = {};
  return {
    async get(key) {
      return Object.hasOwn(data, key) ? { [key]: structuredClone(data[key]) } : {};
    },
    async set(items) {
      Object.assign(data, structuredClone(items));
    },
  };
}

function activity(id: string): ActivityEvent & { readonly id: string } {
  return {
    id,
    source_id: "reddit",
    kind: "view",
    occurred_at: "2026-07-17T10:00:00.000Z",
  };
}

test("only invalid-payload activity responses are terminal", () => {
  for (const status of [400, 413, 422]) {
    assert.equal(isTerminalActivityError({ status }), true, String(status));
  }
  for (const status of [401, 403, 404, 408, 409, 425, 429, 500, 503]) {
    assert.equal(isTerminalActivityError({ status }), false, String(status));
  }
});

test("an invalid activity is dead-lettered so later FIFO records still drain", async () => {
  const storage = memoryStorage();
  const outbox = createDurableOutbox<ActivityEvent & { readonly id: string }>({
    storage,
    storageKey: "events",
  });
  const deadLetterOutbox = createDurableOutbox<{
    readonly id: string;
    readonly event: ActivityEvent & { readonly id: string };
    readonly status: number;
  }>({ storage, storageKey: "dead-letters" });
  await outbox.enqueue(activity("invalid"));
  await outbox.enqueue(activity("later"));

  const delivered: string[] = [];
  await outbox.flush((event) => deliverActivityEvent(
    event,
    async (candidate) => {
      if (candidate.id === "invalid") throw { status: 422 };
      delivered.push(candidate.id!);
    },
    async (candidate, status) => {
      await deadLetterOutbox.enqueue({ id: candidate.id, event: candidate, status });
    },
  ));

  const restartedDeadLetters = createDurableOutbox<{
    readonly id: string;
    readonly event: ActivityEvent & { readonly id: string };
    readonly status: number;
  }>({ storage, storageKey: "dead-letters" });
  assert.deepEqual(await restartedDeadLetters.snapshot(), [{
    id: "invalid",
    event: activity("invalid"),
    status: 422,
  }]);
  assert.deepEqual(delivered, ["later"]);
  assert.equal(await outbox.size(), 0);
});

test("a transient activity failure remains queued and is not dead-lettered", async () => {
  const outbox = createDurableOutbox<ActivityEvent & { readonly id: string }>({
    storage: memoryStorage(),
    storageKey: "events",
  });
  await outbox.enqueue(activity("retry"));
  const deadLetters: string[] = [];

  await assert.rejects(() => outbox.flush((event) => deliverActivityEvent(
    event,
    async () => { throw { status: 503 }; },
    async (candidate) => { deadLetters.push(candidate.id!); },
  )));

  assert.deepEqual(deadLetters, []);
  assert.equal(await outbox.size(), 1);
});
