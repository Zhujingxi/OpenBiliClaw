import test from "node:test";
import assert from "node:assert/strict";

import {
  BUFFER_MAX_SIZE,
  EVENT_BUFFER_KEY,
  PARKED_KEY,
  PARKED_MAX,
  bufferReady,
  drainParkedEvents,
  enqueueEvent,
  getBufferLength,
  parkEvents,
  persistBuffer,
  prependBufferedEvents,
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
  failSet?: boolean;
}

function installStorageStub(options: StorageStubOptions = {}): {
  store: Map<string, unknown>;
  restore: () => void;
} {
  const store = new Map<string, unknown>();
  const original = (globalThis as { chrome?: unknown }).chrome;
  const runtime: { lastError?: { message: string } } = {};
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
          if (options.failSet) {
            throw new Error("storage quota exceeded");
          }
          for (const [k, v] of Object.entries(items)) {
            store.set(k, v);
          }
          callback?.();
        },
        remove(key: string, callback?: () => void): void {
          store.delete(key);
          callback?.();
        },
      },
    },
  };
  (globalThis as { chrome?: unknown }).chrome = chromeStub;
  return {
    store,
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

test("simulated SW restart restores persisted events to the front and clears the mirror key", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    const persisted = [makeEvent("view", "https://www.bilibili.com/video/BV1restore")];
    stub.store.set(EVENT_BUFFER_KEY, persisted);

    await bufferReady();

    assert.equal(getBufferLength(), 1);
    const drained = takeBufferedEvents();
    assert.equal(drained[0].url, "https://www.bilibili.com/video/BV1restore");
    assert.equal(stub.store.has(EVENT_BUFFER_KEY), false, "mirror key must be cleared after restore");
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

test("parkEvents enforces the FIFO cap, dropping the oldest parked events", async () => {
  const stub = installStorageStub();
  __resetBufferForTests();
  try {
    const many = Array.from({ length: PARKED_MAX + 25 }, (_, i) =>
      makeEvent("click", `https://x/${i}`),
    );
    await parkEvents(many);

    const drained = await drainParkedEvents();
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
    await enqueueEvent(makeEvent("view"));
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
