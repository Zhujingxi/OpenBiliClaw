import assert from "node:assert/strict";
import test from "node:test";

import { createDurableOutbox, type OutboxStorage } from "../src/background/durable-outbox.ts";

function memoryStorage(initial: Record<string, unknown> = {}): OutboxStorage & { data: Record<string, unknown> } {
  const data = structuredClone(initial);
  return {
    data,
    async get(key) { return Object.hasOwn(data, key) ? { [key]: structuredClone(data[key]) } : {}; },
    async set(items) { Object.assign(data, structuredClone(items)); },
  };
}

test("durable outbox preserves concurrent enqueues without lost updates", async () => {
  const storage = memoryStorage();
  const outbox = createDurableOutbox<{ id: string; value: number }>({ storage, storageKey: "events" });

  await Promise.all(Array.from({ length: 40 }, (_, value) => outbox.enqueue({ id: String(value), value })));

  assert.equal(await outbox.size(), 40);
  assert.deepEqual((await outbox.snapshot()).map((item) => item.value), Array.from({ length: 40 }, (_, i) => i));
});

test("durable outbox survives restart and retries the identical record after an ambiguous send", async () => {
  const storage = memoryStorage();
  const firstRuntime = createDurableOutbox<{ id: string; payload: unknown }>({ storage, storageKey: "tasks" });
  const completion = { id: "task-1", payload: { lease_token: "lease", result: { items: [{ id: "a" }] } } };
  await firstRuntime.enqueue(completion);
  await assert.rejects(
    () => firstRuntime.flush(async (record) => {
      assert.deepEqual(record, completion);
      throw new TypeError("response was lost after POST");
    }),
    /response was lost/,
  );

  const restartedRuntime = createDurableOutbox<{ id: string; payload: unknown }>({ storage, storageKey: "tasks" });
  const retried: unknown[] = [];
  await restartedRuntime.flush(async (record) => { retried.push(record); });

  assert.deepEqual(retried, [completion]);
  assert.equal(await restartedRuntime.size(), 0);
});

test("a first delivery error retains the entire ordered batch for a later retry", async () => {
  const storage = memoryStorage();
  const outbox = createDurableOutbox<{ id: string }>({ storage, storageKey: "observations" });
  await outbox.enqueue({ id: "one" });
  await outbox.enqueue({ id: "two" });
  await outbox.enqueue({ id: "three" });

  await assert.rejects(() => outbox.flush(async () => { throw new Error("offline"); }), /offline/);
  assert.deepEqual(await outbox.snapshot(), [{ id: "one" }, { id: "two" }, { id: "three" }]);

  const delivered: string[] = [];
  await outbox.flush(async ({ id }) => { delivered.push(id); });
  assert.deepEqual(delivered, ["one", "two", "three"]);
});

test("a concurrent enqueue remains queued while a prior record is being delivered", async () => {
  const storage = memoryStorage();
  const outbox = createDurableOutbox<{ id: string }>({ storage, storageKey: "events" });
  await outbox.enqueue({ id: "first" });
  let release!: () => void;
  const blocked = new Promise<void>((resolve) => { release = resolve; });
  const flush = outbox.flush(async () => blocked);
  await new Promise((resolve) => setTimeout(resolve, 0));
  await outbox.enqueue({ id: "second" });
  release();
  await flush;

  assert.equal(await outbox.size(), 0);
});

test("duplicate stable IDs are stored once", async () => {
  const storage = memoryStorage();
  const outbox = createDurableOutbox<{ id: string; value: number }>({ storage, storageKey: "events" });
  await outbox.enqueue({ id: "same", value: 1 });
  await outbox.enqueue({ id: "same", value: 1 });
  assert.deepEqual(await outbox.snapshot(), [{ id: "same", value: 1 }]);
});
