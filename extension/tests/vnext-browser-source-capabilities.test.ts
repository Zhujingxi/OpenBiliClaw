import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  browserOperationsFromManifests,
  executeBrowserSourceTask,
  LOCAL_BROWSER_SOURCE_OPERATIONS,
} from "../src/background/browser-source-executor.ts";
import {
  createSourceTaskDispatcher,
  type ClaimedSourceTask,
} from "../src/background/generic-source-task-dispatcher.ts";
import type {
  SourceId,
  SourceManifest,
  SourceOperation,
  SourceTransportKind,
} from "../src/shared/api-client.ts";

test("local executors retain all supported browser translations without advertising backend routes", () => {
  assert.deepEqual(LOCAL_BROWSER_SOURCE_OPERATIONS, {
    bilibili: ["search"],
    xiaohongshu: ["bootstrap_import", "search", "creator"],
    douyin: ["bootstrap_import", "search", "trending", "feed"],
    youtube: ["bootstrap_import"],
    zhihu: ["bootstrap_import", "search", "trending", "feed", "creator", "related"],
    reddit: ["bootstrap_import", "search", "trending", "community", "related"],
  });
  assert.equal(LOCAL_BROWSER_SOURCE_OPERATIONS.twitter, undefined);
});

function manifest(
  sourceId: SourceId,
  operations: ReadonlyArray<[
    SourceOperation,
    SourceTransportKind,
    (SourceTransportKind | null)?,
  ]>,
): SourceManifest {
  return {
    source_id: sourceId,
    display_name: sourceId,
    capabilities: [],
    operations: operations.map(([operation, transport_kind, fallback_transport_kind]) => ({
      operation,
      capability: operation === "search" ? "search" : "browser_assisted",
      requires_auth: false,
      result_kind: operation === "bootstrap_import" ? "activity" : "content",
      transport_kind,
      fallback_transport_kind,
    })),
  };
}

const retainedSourceManifests: SourceManifest[] = [
  manifest("bilibili", [
    ["bootstrap_import", "direct"],
    ["search", "direct", "browser"],
    ["trending", "direct"],
  ]),
  manifest("xiaohongshu", [
    ["bootstrap_import", "browser"],
    ["search", "browser"],
    ["creator", "browser"],
  ]),
  manifest("youtube", [
    ["bootstrap_import", "browser"],
    ["search", "direct"],
  ]),
  manifest("twitter", [["search", "cli"]]),
  manifest("zhihu", [
    ["bootstrap_import", "browser"],
    ["search", "browser"],
    ["trending", "browser"],
    ["feed", "browser"],
    ["creator", "browser"],
    ["related", "browser"],
  ]),
  manifest("reddit", [
    ["bootstrap_import", "browser"],
    ["search", "browser"],
    ["trending", "browser"],
    ["community", "browser"],
    ["related", "browser"],
  ]),
];

test("dispatcher operations follow all seven backend manifests and browser fallbacks", () => {
  assert.deepEqual(
    browserOperationsFromManifests([
      ...retainedSourceManifests,
      manifest("douyin", [
        ["bootstrap_import", "browser"],
        ["search", "direct"],
        ["trending", "direct"],
        ["feed", "direct"],
      ]),
    ]),
    {
      bilibili: ["search"],
      xiaohongshu: ["bootstrap_import", "search", "creator"],
      douyin: ["bootstrap_import", "search", "trending", "feed"],
      youtube: ["bootstrap_import"],
      zhihu: ["bootstrap_import", "search", "trending", "feed", "creator", "related"],
      reddit: ["bootstrap_import", "search", "trending", "community", "related"],
    },
  );
});

test("Douyin direct manifest still drains an already-claimed browser search", async () => {
  const directOperations = browserOperationsFromManifests([
    manifest("douyin", [
      ["bootstrap_import", "browser"],
      ["search", "direct"],
      ["trending", "direct"],
      ["feed", "direct"],
    ]),
  ]).douyin;
  assert.deepEqual(directOperations, ["bootstrap_import", "search", "trending", "feed"]);
  const claimed = claimedTask(
    "douyin",
    { operation: "search", query: "persisted-before-switch", limit: 3 },
  );
  const completions: unknown[] = [];
  const dispatcher = createSourceTaskDispatcher({
    sourceId: "douyin",
    operations: directOperations!,
    transport: {
      async claim() {
        return claimed;
      },
      async complete(_taskId, _leaseToken, result) {
        completions.push(result);
      },
      async fail() {
        assert.fail("a locally executable pre-switch row must drain successfully");
      },
    },
    execute: async (task) => ({ operation: task.payload.operation, items: [] }),
  });

  assert.equal(await dispatcher.pollOnce(), true);
  assert.deepEqual(completions, [{ operation: "search", items: [] }]);
});

test("persisted Douyin extension mode dynamically exposes its browser discovery operations", () => {
  const operations = browserOperationsFromManifests([
    manifest("douyin", [
      ["bootstrap_import", "browser"],
      ["search", "browser"],
      ["trending", "browser"],
      ["feed", "browser"],
    ]),
  ]);

  assert.deepEqual(operations, {
    douyin: ["bootstrap_import", "search", "trending", "feed"],
  });
});

type RuntimeListener = (message: Record<string, unknown>) => boolean;

function installChromeTaskHarness(
  onSend: (
    message: Record<string, unknown>,
    emit: (action: string, data: Record<string, unknown>) => void,
  ) => void,
  options: { failSend?: boolean } = {},
): {
  createdUrls: string[];
  updatedUrls: string[];
  removedTabIds: number[];
  sent: Record<string, unknown>[];
  listenerCount(): number;
} {
  const listeners = new Set<RuntimeListener>();
  const createdUrls: string[] = [];
  const updatedUrls: string[] = [];
  const removedTabIds: number[] = [];
  const sent: Record<string, unknown>[] = [];
  const emit = (action: string, data: Record<string, unknown>): void => {
    for (const listener of listeners) listener({ action, data });
  };
  Object.assign(globalThis, {
    chrome: {
      tabs: {
        create: async ({ url }: { url: string }) => {
          createdUrls.push(url);
          return { id: 17, status: "complete" };
        },
        update: async (_tabId: number, { url }: { url: string }) => {
          updatedUrls.push(url);
          return { id: 17, status: "complete" };
        },
        remove: async (tabId: number) => {
          removedTabIds.push(tabId);
        },
        get: async () => ({ id: 17, status: "complete" }),
        sendMessage: async (_tabId: number, message: Record<string, unknown>) => {
          sent.push(message);
          if (options.failSend) throw new Error("content script unavailable");
          queueMicrotask(() => onSend(message, emit));
          return undefined;
        },
        onUpdated: {
          addListener: () => undefined,
          removeListener: () => undefined,
        },
      },
      runtime: {
        onMessage: {
          addListener: (listener: RuntimeListener) => listeners.add(listener),
          removeListener: (listener: RuntimeListener) => listeners.delete(listener),
        },
      },
    },
  });
  return {
    createdUrls,
    updatedUrls,
    removedTabIds,
    sent,
    listenerCount: () => listeners.size,
  };
}

function xhsTask(payload: ClaimedSourceTask["payload"]): ClaimedSourceTask {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    source_id: "xiaohongshu",
    payload,
    lease_token: "12345678901234567890",
    lease_expires_at: "2030-01-01T00:00:00Z",
    request_deadline_at: "2030-01-01T00:05:00Z",
  };
}

function claimedTask(
  source_id: ClaimedSourceTask["source_id"],
  payload: ClaimedSourceTask["payload"],
): ClaimedSourceTask {
  return { ...xhsTask(payload), source_id };
}

test("six browser-assisted sources execute typed translations and Twitter remains passive-only", async () => {
  const resultActionByAction: Record<string, [string, string]> = {
    BILI_TASK_EXECUTE: ["BILI_TASK_RESULT", "videos"],
    XHS_TASK_EXECUTE: ["XHS_TASK_RESULT", "notes"],
    DY_SCOPE_EXECUTE: ["DY_SCOPE_RESULT", "items"],
    DY_SEARCH_EXECUTE: ["DY_SEARCH_RESULT", "items"],
    DY_HOT_EXECUTE: ["DY_HOT_RESULT", "items"],
    DY_FEED_EXECUTE: ["DY_FEED_RESULT", "items"],
    YT_SCOPE_EXECUTE: ["YT_SCOPE_RESULT", "items"],
    ZHIHU_TASK_EXECUTE: ["ZHIHU_TASK_RESULT", "items"],
    REDDIT_TASK_EXECUTE: ["REDDIT_TASK_RESULT", "items"],
  };
  const sentActions: string[] = [];
  installChromeTaskHarness((message, emit) => {
    const action = String(message.action);
    sentActions.push(action);
    const [resultAction, field] = resultActionByAction[action]!;
    const data = message.data as Record<string, unknown>;
    emit(resultAction, {
      task_id: String(data.task_id),
      status: "ok",
      [field]: [{ translated_by: action }],
    });
  });

  const cases: Array<[ClaimedSourceTask, number]> = [
    [claimedTask("bilibili", { operation: "search", query: "typed", limit: 2 }), 1],
    [claimedTask("xiaohongshu", { operation: "search", query: "typed", limit: 2 }), 1],
    [claimedTask("douyin", { operation: "bootstrap_import", limit: 2 }), 4],
    [claimedTask("douyin", { operation: "search", query: "typed", limit: 2 }), 1],
    [claimedTask("douyin", { operation: "trending", limit: 2 }), 1],
    [claimedTask("douyin", { operation: "feed", limit: 2 }), 1],
    [claimedTask("youtube", { operation: "bootstrap_import", limit: 2 }), 3],
    [claimedTask("zhihu", { operation: "search", query: "typed", limit: 2 }), 1],
    [claimedTask("reddit", { operation: "search", query: "typed", limit: 2 }), 1],
  ];
  for (const [task, expectedItems] of cases) {
    const result = await executeBrowserSourceTask(task);
    assert.equal(result.operation, task.payload.operation);
    assert.equal(result.items.length, expectedItems);
  }
  assert.deepEqual(sentActions, [
    "BILI_TASK_EXECUTE",
    "XHS_TASK_EXECUTE",
    "DY_SCOPE_EXECUTE",
    "DY_SCOPE_EXECUTE",
    "DY_SCOPE_EXECUTE",
    "DY_SCOPE_EXECUTE",
    "DY_SEARCH_EXECUTE",
    "DY_HOT_EXECUTE",
    "DY_FEED_EXECUTE",
    "YT_SCOPE_EXECUTE",
    "YT_SCOPE_EXECUTE",
    "YT_SCOPE_EXECUTE",
    "ZHIHU_TASK_EXECUTE",
    "REDDIT_TASK_EXECUTE",
  ]);

  await assert.rejects(
    () => executeBrowserSourceTask(
      claimedTask("twitter", { operation: "search", query: "typed", limit: 2 }),
    ),
    /does not declare browser-assisted execution/,
  );
});

test("Xiaohongshu bootstrap aggregates partial batches and follows next_url in the same tab", async () => {
  let dispatch = 0;
  const harness = installChromeTaskHarness((_message, emit) => {
    dispatch += 1;
    if (dispatch === 1) {
      emit("XHS_TASK_RESULT", {
        task_id: "11111111-1111-4111-8111-111111111111",
        status: "partial",
        notes: [{ note_id: "partial-a" }],
      });
      emit("XHS_TASK_RESULT", {
        task_id: "11111111-1111-4111-8111-111111111111",
        status: "empty",
        notes: [],
        next_url: "https://www.xiaohongshu.com/user/profile/writer-id",
      });
      return;
    }
    emit("XHS_TASK_RESULT", {
      task_id: "11111111-1111-4111-8111-111111111111",
      status: "partial",
      notes: [{ note_id: "partial-b" }],
    });
    emit("XHS_TASK_RESULT", {
      task_id: "11111111-1111-4111-8111-111111111111",
      status: "ok",
      notes: [{ note_id: "final-c" }],
    });
  });

  const result = await executeBrowserSourceTask(
    xhsTask({ operation: "bootstrap_import", limit: 10 }),
  );

  assert.deepEqual(result.items.map((item) => item.note_id), ["partial-a", "partial-b", "final-c"]);
  assert.deepEqual(harness.updatedUrls, [
    "https://www.xiaohongshu.com/user/profile/writer-id",
  ]);
  assert.equal(harness.sent.length, 2);
});

test("Xiaohongshu creator identifiers become safe profile URLs", async () => {
  const harness = installChromeTaskHarness((_message, emit) => {
    emit("XHS_TASK_RESULT", {
      task_id: "11111111-1111-4111-8111-111111111111",
      status: "empty",
      notes: [],
    });
  });

  await executeBrowserSourceTask(xhsTask({ operation: "creator", creator: "writer-id", limit: 5 }));
  assert.deepEqual(harness.createdUrls, [
    "https://www.xiaohongshu.com/user/profile/writer-id",
  ]);
});

test("Xiaohongshu creator accepts valid source profile URLs without changing them", async () => {
  const harness = installChromeTaskHarness((_message, emit) => {
    emit("XHS_TASK_RESULT", {
      task_id: "11111111-1111-4111-8111-111111111111",
      status: "empty",
      notes: [],
    });
  });
  const profileUrl = "https://www.xiaohongshu.com/user/profile/writer-id";

  await executeBrowserSourceTask(xhsTask({ operation: "creator", creator: profileUrl, limit: 5 }));
  assert.deepEqual(harness.createdUrls, [profileUrl]);
});

test("content delivery failure immediately removes the pending result listener", async () => {
  const controller = new AbortController();
  const harness = installChromeTaskHarness(() => undefined, { failSend: true });
  const realNow = Date.now;
  let fakeNow = realNow();
  Date.now = () => {
    fakeNow += 9_000;
    return fakeNow;
  };
  try {
    await assert.rejects(
      () => executeBrowserSourceTask(
        claimedTask("bilibili", { operation: "search", query: "typed", limit: 2 }),
        controller.signal,
      ),
      /content script unavailable/,
    );
    assert.equal(harness.listenerCount(), 0);
    assert.deepEqual(harness.removedTabIds, [17]);
  } finally {
    Date.now = realNow;
    controller.abort();
  }
});

test("every retained executor and Twitter passive collector remains in the build graph", () => {
  const contentContracts = {
    bilibili: "installBiliMessageListener",
    xiaohongshu: "registerTaskExecutor",
    douyin: "registerDyScopeExecutor",
    youtube: "installYtMessageListener",
    zhihu: "installZhihuMessageListener",
    reddit: "installRedditMessageListener",
    x: "startCollector(twitterAdapter)",
  };
  const manifest = readFileSync(resolve("manifest.json"), "utf8");
  const build = readFileSync(resolve("scripts/build.mjs"), "utf8");
  const worker = readFileSync(resolve("src/background/service-worker.ts"), "utf8");
  assert.match(worker, /createSourceTaskDispatcher/);
  assert.match(worker, /executeBrowserSourceTask/);
  assert.match(
    worker,
    /catch \{\s+sourceTaskDispatchers = \[\];\s+backendReachable = false;\s+return;/,
    "a failed manifest refresh must not claim through stale dispatchers",
  );
  for (const [source, marker] of Object.entries(contentContracts)) {
    const sourceText = readFileSync(resolve(`src/content/${source}.ts`), "utf8");
    assert.match(sourceText, new RegExp(marker.replace(/[()]/g, "\\$&")));
    assert.match(manifest, new RegExp(`dist/content/${source}\\.js`));
    assert.match(build, new RegExp(`src/content/${source}\\.ts`));
  }
});

test("Xiaohongshu keeps page-derived xsec tokens without the removed network sniffer", () => {
  const entry = readFileSync(resolve("src/content/xiaohongshu.ts"), "utf8");
  const bootstrap = readFileSync(resolve("src/content/xhs/bootstrap.ts"), "utf8");
  assert.match(entry, /XHS_URLS_OBSERVED/);
  assert.match(entry, /xsec_token/);
  assert.match(bootstrap, /noteCard.*xsec_token/s);
  assert.equal(readFileSync(resolve("scripts/build.mjs"), "utf8").includes("xhs-token-sniffer"), false);
});
