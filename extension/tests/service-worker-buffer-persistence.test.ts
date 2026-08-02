import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  BUFFER_MAX_SIZE,
  EVENT_BUFFER_KEY,
  INFLIGHT_KEY,
  PARKED_KEY,
  PARKED_MAX,
  bufferReady,
  claimBufferedEventsForFlush,
  completeInflightEvents,
  drainParkedEvents,
  enqueueEvent,
  enqueueEventWithDurableAck,
  getBufferLength,
  parkEvents,
  persistBuffer,
  prependBufferedEvents,
  recoverParkedEventsForFlush,
  takeBufferedEvents,
  __resetBufferForTests,
} from "../src/background/buffer.ts";
import type { BehaviorEvent } from "../src/shared/types.ts";

function makeEvent(type: string, url = "https://www.bilibili.com/video/BV1AB411c7mD"): BehaviorEvent {
  return {
    type,
    url,
    title: "示例视频",
    timestamp: Date.now(),
    source_platform: "bilibili",
    context: {
      pageType: "video",
      viewport: { width: 1440, height: 900 },
      scrollPosition: 0,
    },
    metadata: {},
  };
}

interface StorageStubOptions {
  getDelayMs?: number;
  setDelayMs?: number;
  failSet?: boolean;
}

function installStorageStub(options: StorageStubOptions = {}): {
  store: Map<string, unknown>;
  failNextSet: () => void;
  failNextRemove: () => void;
  restore: () => void;
} {
  const store = new Map<string, unknown>();
  const original = (globalThis as { chrome?: unknown }).chrome;
  const runtime: { lastError?: { message: string } } = {};
  let failNextSet = false;
  let failNextRemove = false;
  const chromeStub = {
    runtime,
    storage: {
      local: {
        get(key: string, callback: (items: Record<string, unknown>) => void): void {
          const deliver = (): void => callback({ [key]: store.get(key) });
          if (options.getDelayMs && options.getDelayMs > 0) {
            setTimeout(deliver, options.getDelayMs);
          } else {
            queueMicrotask(deliver);
          }
        },
        set(items: Record<string, unknown>, callback?: () => void): void {
          if (options.failSet || failNextSet) {
            failNextSet = false;
            throw new Error("storage quota exceeded");
          }
          const apply = (): void => {
            for (const [k, v] of Object.entries(items)) {
              store.set(k, v);
            }
            callback?.();
          };
          if (options.setDelayMs && options.setDelayMs > 0) setTimeout(apply, options.setDelayMs);
          else apply();
        },
        remove(key: string, callback?: () => void): void {
          if (failNextRemove) {
            failNextRemove = false;
            throw new Error("simulated worker stop before remove");
          }
          store.delete(key);
          callback?.();
        },
      },
    },
  };
  (globalThis as { chrome?: unknown }).chrome = chromeStub;
  return {
    store,
    failNextSet() {
      failNextSet = true;
    },
    failNextRemove() {
      failNextRemove = true;
    },
    restore() {
      (globalThis as { chrome?: unknown }).chrome = original;
    },
  };
}

function captureWarnings(): { messages: string[]; restore: () => void } {
  const messages: string[] = [];
  const originalWarn = console.warn;
  console.warn = (...args: unknown[]): void => {
    messages.push(args.map((a) => String(a)).join(" "));
  };
  return {
    messages,
    restore() {
      console.warn = originalWarn;
    },
  };
}

test("enqueueEvent awaits the storage mirror write-through before resolving", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    await enqueueEvent(makeEvent("view"));
    const mirrored = stub.store.get(EVENT_BUFFER_KEY) as BehaviorEvent[] | undefined;
    assert.ok(Array.isArray(mirrored), "buffer must be mirrored to storage");
    assert.equal(mirrored.length, 1);
    assert.equal(mirrored[0].type, "view");
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("BEHAVIOR_EVENT keeps the message port alive and ACKs only after the mirror commits", async () => {
  const stub = installStorageStub({ setDelayMs: 20 });
  __resetBufferForTests();
  try {
    const responses: Array<{ ok: boolean; error?: string }> = [];
    let resolveAck: (() => void) | undefined;
    const acked = new Promise<void>((resolve) => {
      resolveAck = resolve;
    });

    const keepAlive = enqueueEventWithDurableAck(makeEvent("like"), (response) => {
      responses.push(response);
      resolveAck?.();
    });

    assert.equal(keepAlive, true, "listener must keep the MV3 message port alive");
    assert.deepEqual(responses, [], "no ACK is allowed before storage finishes");
    assert.equal(stub.store.has(EVENT_BUFFER_KEY), false);

    await acked;

    assert.deepEqual(responses, [{ ok: true }]);
    const mirrored = stub.store.get(EVENT_BUFFER_KEY) as BehaviorEvent[];
    assert.equal(mirrored.length, 1, "success ACK follows the durable mirror");
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("service-worker BEHAVIOR_EVENT listener returns the durable ACK keepalive", () => {
  const source = readFileSync(resolve("src", "background", "service-worker.ts"), "utf8");
  const branch = source.split('if (message.action !== "BEHAVIOR_EVENT") return;', 2)[1]?.slice(0, 700);

  assert.ok(branch);
  assert.match(branch, /return enqueueEventWithDurableAck\(event, sendResponse/);
  assert.match(branch, /void flushEvents\(\)/);
});

test("BEHAVIOR_EVENT returns a stable failure ACK when its mirror cannot commit", async () => {
  const stub = installStorageStub({ failSet: true });
  __resetBufferForTests();
  try {
    const response = await new Promise<{ ok: boolean; error?: string }>((resolve) => {
      const keepAlive = enqueueEventWithDurableAck(makeEvent("favorite"), resolve);
      assert.equal(keepAlive, true);
    });

    assert.deepEqual(response, { ok: false, error: "persist_failed" });
    assert.equal(getBufferLength(), 1, "the live worker still retains a retry copy");
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("simulated SW restart writes restored legacy IDs through and preserves them across another restart", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    const persisted = [makeEvent("view", "https://www.bilibili.com/video/BV1restore")];
    stub.store.set(EVENT_BUFFER_KEY, persisted);

    await bufferReady();

    assert.equal(getBufferLength(), 1);
    const drained = takeBufferedEvents();
    assert.equal(drained[0].url, "https://www.bilibili.com/video/BV1restore");
    const firstId = drained[0].event_id;
    assert.ok(firstId);
    const mirrored = stub.store.get(EVENT_BUFFER_KEY) as BehaviorEvent[];
    assert.equal(mirrored[0].event_id, firstId, "generated ID must be written through immediately");

    __resetBufferForTests();
    await bufferReady();
    const restoredAgain = takeBufferedEvents();
    assert.equal(restoredAgain.length, 1);
    assert.equal(restoredAgain[0].event_id, firstId);
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("an enqueue racing the restore is not lost and not overwritten by the restore", async () => {
  const stub = installStorageStub({ getDelayMs: 20 });
  __resetBufferForTests();
  try {
    const persisted = [makeEvent("favorite", "https://www.bilibili.com/video/BV1restored")];
    stub.store.set(EVENT_BUFFER_KEY, persisted);

    // Kick off the restore gate but do NOT await it before enqueueing.
    const ready = bufferReady();
    const enqueued = enqueueEvent(makeEvent("view", "https://www.bilibili.com/video/BV1live"));

    await Promise.all([ready, enqueued]);

    const remaining = takeBufferedEvents();
    const urls = remaining.map((e) => e.url);
    assert.ok(urls.includes("https://www.bilibili.com/video/BV1restored"), "restored event survives");
    assert.ok(urls.includes("https://www.bilibili.com/video/BV1live"), "raced enqueue survives");
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("claimed HTTP batch remains durable inflight and keeps its event_id across worker restart", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    await enqueueEvent(makeEvent("like", "https://x/inflight"));
    const [claimed] = await claimBufferedEventsForFlush();
    assert.ok(claimed.event_id);
    assert.equal((stub.store.get(EVENT_BUFFER_KEY) as BehaviorEvent[]).length, 0);
    assert.equal((stub.store.get(INFLIGHT_KEY) as BehaviorEvent[])[0].event_id, claimed.event_id);

    __resetBufferForTests();
    await bufferReady();
    const [retried] = await claimBufferedEventsForFlush();
    assert.equal(retried.event_id, claimed.event_id);
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("failed inflight completion retains the in-memory owner for same-worker retry", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    await enqueueEvent(makeEvent("favorite", "https://x/ack-fail"));
    const [claimed] = await claimBufferedEventsForFlush();
    stub.failNextSet();
    await assert.rejects(completeInflightEvents());
    const [retry] = await claimBufferedEventsForFlush();
    assert.equal(retry.event_id, claimed.event_id);
    await completeInflightEvents();
    assert.equal((stub.store.get(INFLIGHT_KEY) as BehaviorEvent[]).length, 0);
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("serialized enqueue and inflight completion cannot stale-overwrite the live mirror", async () => {
  const stub = installStorageStub({ setDelayMs: 5 });
  __resetBufferForTests();
  try {
    await enqueueEvent(makeEvent("like", "https://x/first"));
    await claimBufferedEventsForFlush();
    const enqueueSecond = enqueueEvent(makeEvent("comment", "https://x/second"));
    const completeFirst = completeInflightEvents();
    await Promise.all([enqueueSecond, completeFirst]);

    const live = stub.store.get(EVENT_BUFFER_KEY) as BehaviorEvent[];
    assert.deepEqual(live.map((event) => event.url), ["https://x/second"]);
    assert.equal((stub.store.get(INFLIGHT_KEY) as BehaviorEvent[]).length, 0);
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("persistBuffer rewrites the mirror from the post-flush buffer state", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    await enqueueEvent(makeEvent("view"));
    // Simulate a successful flush: buffer drained, then parked events prepended back.
    takeBufferedEvents();
    prependBufferedEvents([makeEvent("favorite", "https://www.bilibili.com/video/BV1parked")]);
    await persistBuffer();

    const mirrored = stub.store.get(EVENT_BUFFER_KEY) as BehaviorEvent[];
    assert.equal(mirrored.length, 1);
    assert.equal(mirrored[0].url, "https://www.bilibili.com/video/BV1parked");
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("parkEvents stores a not_initialized batch and drainParkedEvents returns it oldest-first then deletes the key", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    await parkEvents([makeEvent("click", "https://x/1"), makeEvent("scroll", "https://x/2")]);
    await parkEvents([makeEvent("hover", "https://x/3")]);

    assert.ok(stub.store.has(PARKED_KEY));

    const drained = await drainParkedEvents();
    assert.deepEqual(
      drained.map((e) => e.url),
      ["https://x/1", "https://x/2", "https://x/3"],
    );
    assert.equal(stub.store.has(PARKED_KEY), false, "parked key deleted after drain");
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("legacy parked ID is durable before live transfer, so a crash replays only the same identity", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    stub.store.set(PARKED_KEY, [{ parkedAt: Date.now(), event: makeEvent("click", "https://x/legacy") }]);
    await bufferReady();
    stub.failNextRemove();
    await drainParkedEvents();

    const parkedAfterCrash = stub.store.get(PARKED_KEY) as Array<{ event: BehaviorEvent }>;
    const liveAfterCrash = stub.store.get(EVENT_BUFFER_KEY) as BehaviorEvent[];
    assert.ok(parkedAfterCrash[0].event.event_id);
    assert.equal(parkedAfterCrash[0].event.event_id, liveAfterCrash[0].event_id);

    __resetBufferForTests();
    await bufferReady();
    await drainParkedEvents();
    const retried = await claimBufferedEventsForFlush();
    assert.equal(retried.length, 1);
    assert.equal(retried[0].event_id, liveAfterCrash[0].event_id);
    assert.equal(stub.store.has(PARKED_KEY), false);
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("initialized recovery promotes parked-only work without waiting for a new event", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    await parkEvents([makeEvent("click", "https://x/parked-only")]);
    assert.equal(getBufferLength(), 0);
    assert.equal(await recoverParkedEventsForFlush(), 1);
    const batch = await claimBufferedEventsForFlush();
    assert.deepEqual(batch.map((event) => event.url), ["https://x/parked-only"]);
    assert.equal(stub.store.has(PARKED_KEY), false);
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("parkEvents enforces the FIFO cap, dropping the oldest parked events", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    const many = Array.from({ length: PARKED_MAX + 25 }, (_, i) =>
      makeEvent("click", `https://x/${i}`),
    );
    await parkEvents(many);

    const drained: BehaviorEvent[] = [];
    while (stub.store.has(PARKED_KEY)) {
      drained.push(...await drainParkedEvents());
      takeBufferedEvents();
      await persistBuffer();
    }
    assert.equal(drained.length, PARKED_MAX);
    // Oldest 25 dropped; newest survive in order.
    assert.equal(drained[0].url, "https://x/25");
    assert.equal(drained[drained.length - 1].url, `https://x/${PARKED_MAX + 24}`);
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("drainParkedEvents drops entries older than the 48h TTL", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    const stale = { parkedAt: Date.now() - 49 * 3_600_000, event: makeEvent("click", "https://x/stale") };
    const fresh = { parkedAt: Date.now(), event: makeEvent("click", "https://x/fresh") };
    stub.store.set(PARKED_KEY, [stale, fresh]);

    const drained = await drainParkedEvents();
    assert.deepEqual(
      drained.map((e) => e.url),
      ["https://x/fresh"],
    );
  } finally {
    stub.restore();
    __resetBufferForTests();
  }
});

test("the combined buffer never exceeds BUFFER_MAX_SIZE and evictions are logged", async () => {
  const stub = installStorageStub();
  const warn = captureWarnings();
  __resetBufferForTests();
  try {
    for (let i = 0; i < BUFFER_MAX_SIZE + 10; i += 1) {
      await enqueueEvent(makeEvent("view", `https://www.bilibili.com/video/BV${i}`));
    }
    assert.equal(getBufferLength(), BUFFER_MAX_SIZE);
    const mirrored = stub.store.get(EVENT_BUFFER_KEY) as BehaviorEvent[];
    assert.equal(mirrored.length, BUFFER_MAX_SIZE);
    assert.ok(
      warn.messages.some((m) => m.toLowerCase().includes("evict")),
      "eviction must be logged",
    );
  } finally {
    warn.restore();
    stub.restore();
    __resetBufferForTests();
  }
});

test("a storage.set rejection is logged and leaves the in-memory buffer intact", async () => {
  const stub = installStorageStub({ failSet: true });
  const warn = captureWarnings();
  __resetBufferForTests();
  try {
    await assert.rejects(enqueueEvent(makeEvent("view")));
    assert.equal(getBufferLength(), 1, "memory buffer must survive a failed mirror write");
    assert.ok(
      warn.messages.some((m) => m.toLowerCase().includes("persist") || m.toLowerCase().includes("storage")),
      "the failed write must be logged",
    );
  } finally {
    warn.restore();
    stub.restore();
    __resetBufferForTests();
  }
});
