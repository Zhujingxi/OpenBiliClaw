import test from "node:test";
import assert from "node:assert/strict";

import {
  appendRecommendations,
  cacheConfigSnapshot,
  applyBackendUpdate,
  checkBackendStatus,
  checkBackendUpdate,
  fetchPendingDelight,
  fetchPendingDelightBatch,
  fetchActivityFeed,
  fetchChatTurn,
  fetchChatTurns,
  fetchConfig,
  fetchHealth,
  fetchProfileSummary,
  fetchSourceShareSuggestion,
  fetchUpdateStatus,
  probeConfigService,
  readCachedConfigSnapshot,
  requestJson,
  reshuffleRecommendations,
  respondToAvoidanceProbe,
  startChatTurn,
  submitInsightFeedback,
  updateConfig,
  __resetPopupHealthCacheForTests,
} from "../popup/popup-api.js";
import * as popupApi from "../popup/popup-api.js";
import { __resetBackendEndpointForTests } from "../popup/popup-backend-config.js";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function modelSnapshotFixture(ids = ["a"], revision = "revision-a") {
  return {
    revision,
    source: "native",
    models: {
      schema_version: 1,
      chat: {
        connections: ids.map((id) => ({
          id,
          name: id,
          type: "openai_compatible",
          preset: "custom",
          model: "test-model",
          base_url: "https://example.test/v1",
          credential: { source: "none", configured: false },
          api_mode: "chat_completions",
          reasoning_effort: "",
          http_referer: "",
          x_title: "",
          num_ctx: 0,
        })),
        concurrency: 4,
        timeout_seconds: 300,
      },
      embedding: {
        enabled: false,
        settings: {
          model: "bge-m3",
          output_dimensionality: 1024,
          similarity_threshold: 0.82,
          multimodal_enabled: false,
        },
        providers: [],
      },
    },
    migration: { state: "none", confirmed: true, issues: [] },
    overrides: [],
  };
}

test("health helpers coalesce concurrent popup probes", async () => {
  __resetPopupHealthCacheForTests();
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = (async (url: string, options: RequestInit = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { status: "ok", service: "openbiliclaw-api", embedding_ready: true };
      },
    };
  }) as unknown as typeof fetch;

  const [health, healthAgain] = await Promise.all([fetchHealth(), fetchHealth()]);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/health");
  assert.deepEqual(health, { status: "ok", service: "openbiliclaw-api", embedding_ready: true });
  assert.deepEqual(healthAgain, health);
});

test("health helpers reuse a fresh popup health result", async () => {
  __resetPopupHealthCacheForTests();
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = (async (url: string, options: RequestInit = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { status: "ok", service: "openbiliclaw-api", embedding_ready: false };
      },
    };
  }) as unknown as typeof fetch;

  const health = await fetchHealth();
  const secondHealth = await fetchHealth();

  assert.equal(calls.length, 1);
  assert.equal(health?.embedding_ready, false);
  assert.deepEqual(secondHealth, health);
});

test("checkBackendStatus probes the lightweight /ping endpoint, not /health", async () => {
  __resetPopupHealthCacheForTests();
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = (async (url: string, options: RequestInit = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { status: "ok", service: "openbiliclaw-api" };
      },
    };
  }) as unknown as typeof fetch;

  const online = await checkBackendStatus();

  assert.equal(online, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/ping");
});

test("checkBackendStatus falls back to /health when /ping is missing (older backend)", async () => {
  __resetPopupHealthCacheForTests();
  const calls: string[] = [];
  globalThis.fetch = (async (url: string) => {
    calls.push(url);
    if (url.endsWith("/ping")) {
      return {
        ok: false,
        status: 404,
        async json() {
          return { error: "not_found" };
        },
      };
    }
    return {
      ok: true,
      status: 200,
      async json() {
        return { status: "ok", service: "openbiliclaw-api" };
      },
    };
  }) as unknown as typeof fetch;

  const online = await checkBackendStatus();

  assert.equal(online, true);
  assert.deepEqual(calls, ["http://127.0.0.1:8420/api/ping", "http://127.0.0.1:8420/api/health"]);
});

test("checkBackendStatus reports offline when the ping request rejects", async () => {
  __resetPopupHealthCacheForTests();
  globalThis.fetch = (async () => {
    throw new TypeError("Failed to fetch");
  }) as unknown as typeof fetch;

  assert.equal(await checkBackendStatus(), false);
});

test("reshuffleRecommendations posts to reshuffle endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          items: [
            {
              id: 11,
              bvid: "BV1NEW",
              title: "新的一批",
              up_name: "UPA",
              cover_url: "//i0.hdslb.com/bfs/archive/new-cover.jpg",
              expression: "先给你捞一条新的。",
              topic_label: "",
              presented: false,
            },
          ],
        };
      },
    };
  };

  const result = await reshuffleRecommendations();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/recommendations/reshuffle");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(result, {
    items: [
      {
        id: 11,
        bvid: "BV1NEW",
        title: "新的一批",
        up_name: "UPA",
        cover_url: "https://i0.hdslb.com/bfs/archive/new-cover.jpg",
        expression: "先给你捞一条新的。",
        topic_label: "",
        presented: false,
        item_key: "",
        content_id: "BV1NEW",
        content_url: "",
        source_platform: "bilibili",
        content_type: "video",
        body_text: "",
        published_at: "",
        published_label: "",
        view_count: 0,
        like_count: 0,
        comment_count: 0,
        favorite_count: 0,
        danmaku_count: 0,
      },
    ],
  });
});

test("appendRecommendations posts excluded bvids to append endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          items: [
            {
              id: 21,
              bvid: "BV1APPEND",
              title: "追加的一条",
              up_name: "UPB",
              cover_url: "http://i0.hdslb.com/bfs/archive/append-cover.jpg",
              expression: "",
              topic_label: "",
              presented: false,
            },
          ],
        };
      },
    };
  };

  const result = await appendRecommendations(["BV1A", "BV1B"]);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/recommendations/append");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.equal(calls[0].options.body, JSON.stringify({ excluded_bvids: ["BV1A", "BV1B"] }));
  assert.deepEqual(result, {
    items: [
      {
        id: 21,
        bvid: "BV1APPEND",
        title: "追加的一条",
        up_name: "UPB",
        cover_url: "https://i0.hdslb.com/bfs/archive/append-cover.jpg",
        expression: "",
        topic_label: "",
        presented: false,
        item_key: "",
        content_id: "BV1APPEND",
        content_url: "",
        source_platform: "bilibili",
        content_type: "video",
        body_text: "",
        published_at: "",
        published_label: "",
        view_count: 0,
        like_count: 0,
        comment_count: 0,
        favorite_count: 0,
        danmaku_count: 0,
      },
    ],
  });
});

test("respondToAvoidanceProbe posts to avoidance probe endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { ok: true };
      },
    };
  };

  await respondToAvoidanceProbe("浅层热点复读", "confirm", "对，这类我不想看");

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/avoidance-probes/respond");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    domain: "浅层热点复读",
    response: "confirm",
    message: "对，这类我不想看",
  });
});

test("submitInsightFeedback posts hypothesis + signal to the insights endpoint", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { ok: true, matched: true, validated: false, confidence: 0.35 };
      },
    };
  };

  const res = await submitInsightFeedback("用户可能通过深度内容获得掌控感。", "reject");

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/insights/feedback");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    hypothesis: "用户可能通过深度内容获得掌控感。",
    signal: "reject",
  });
  assert.equal(res.matched, true);
  assert.equal(res.confidence, 0.35);
});

test("fetchRecommendations normalizes cover urls from the recommend endpoint", async () => {
  globalThis.fetch = async () => ({
    ok: true,
    async json() {
      return {
        items: [
          {
            id: 31,
            bvid: "BV1FETCH",
            title: "初始推荐",
            up_name: "UPC",
            cover_url: "http://i1.hdslb.com/bfs/archive/fetch-cover.jpg",
            expression: "",
            topic_label: "",
            presented: 0,
          },
        ],
      };
    },
  });

  const { fetchRecommendations } = await import("../popup/popup-api.js");
  const result = await fetchRecommendations();

  assert.deepEqual(result, [
    {
      id: 31,
      bvid: "BV1FETCH",
      title: "初始推荐",
      up_name: "UPC",
      cover_url: "https://i1.hdslb.com/bfs/archive/fetch-cover.jpg",
      expression: "",
      topic_label: "",
      presented: false,
      item_key: "",
      content_id: "BV1FETCH",
      content_url: "",
      source_platform: "bilibili",
      content_type: "video",
      body_text: "",
      published_at: "",
      published_label: "",
      view_count: 0,
      like_count: 0,
      comment_count: 0,
      favorite_count: 0,
      danmaku_count: 0,
    },
  ]);
});

test("fetchActivityFeed loads popup activity summaries", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          live_summary: "正在补候选",
          headline: "阿B 刚记下了你最近更吃深拆",
          items: [],
        };
      },
    };
  };

  const result = await fetchActivityFeed();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/activity-feed");
  assert.equal(calls[0].options.method, "GET");
  assert.deepEqual(result, {
    live_summary: "正在补候选",
    headline: "阿B 刚记下了你最近更吃深拆",
    items: [],
  });
});

test("backend update API helpers use backend-only update endpoints", async () => {
  const calls: { url: string; options: RequestInit }[] = [];
  globalThis.fetch = (async (url: string, options: RequestInit = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        if (url.endsWith("/api/update/apply")) {
          return { target: "backend", state: "applying", reason: "none", accepted: true };
        }
        return {
          backend: {
            state: "update_available",
            latest_tag: "backend-v0.3.92",
          },
        };
      },
    };
  }) as unknown as typeof fetch;

  await fetchUpdateStatus();
  await checkBackendUpdate();
  await applyBackendUpdate("backend-v0.3.92");

  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/update-status");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[1].url, "http://127.0.0.1:8420/api/update/check");
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[1].options.body, JSON.stringify({ include_backend: true }));
  assert.equal(calls[2].url, "http://127.0.0.1:8420/api/update/apply");
  assert.equal(calls[2].options.method, "POST");
  assert.equal(
    calls[2].options.body,
    JSON.stringify({ target: "backend", tag: "backend-v0.3.92" }),
  );
});

test("fetchPendingDelight loads the current pending delight candidate", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          item: {
            bvid: "BV1DELIGHT",
            title: "你可能会意外喜欢的这条",
            delight_reason: "它和你最近的节奏不完全一样，但入口很对味。",
            delight_score: 0.78,
            delight_hook: "换个方向试试",
            cover_url: "//i0.hdslb.com/bfs/archive/delight-cover.jpg",
          },
        };
      },
    };
  };

  const result = await fetchPendingDelight();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/delight/pending");
  assert.equal(calls[0].options.method, "GET");
  assert.deepEqual(result, {
    bvid: "BV1DELIGHT",
    title: "你可能会意外喜欢的这条",
    delight_reason: "它和你最近的节奏不完全一样，但入口很对味。",
    delight_score: 0.78,
    delight_hook: "换个方向试试",
    cover_url: "//i0.hdslb.com/bfs/archive/delight-cover.jpg",
  });
});

test("fetchPendingDelightBatch omits limit by default so backend config applies", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return {
      ok: true,
      async json() {
        return { items: [] };
      },
    } as Response;
  };

  await fetchPendingDelightBatch();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/delight/pending-batch");
  assert.equal(calls[0].options.method, "GET");
});

test("fetchPendingDelightBatch still forwards an explicit limit override", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    return {
      ok: true,
      async json() {
        return { items: [] };
      },
    } as Response;
  };

  await fetchPendingDelightBatch(11);

  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/delight/pending-batch?limit=11");
});

test("popup delight queue fetches do not hardcode the old fixed batch size", () => {
  const popupJs = readFileSync(resolve("popup/popup.ts"), "utf8");

  assert.doesNotMatch(popupJs, /fetchPendingDelightBatch\(20\)/);
  assert.match(popupJs, /fetchPendingDelightBatch\(\)/);
});

test("fetchProfileSummary forwards limit and cursor for cognition history pagination", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          initialized: true,
          recent_cognition_updates: [],
          has_more_cognition_updates: false,
          next_cognition_cursor: "",
        };
      },
    };
  };

  await fetchProfileSummary({ limit: 3, cursor: "6" });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/profile-summary?limit=3&cursor=6");
  assert.equal(calls[0].options.method, "GET");
});

test("fetchConfig sends GET to /config without reveal_keys (masked secrets only)", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          language: "zh",
          bilibili: { cookie: "SESS****masked" },
          llm: {
            default_provider: "gemini",
            gemini: { api_key: "", model: "gemini-2.5-flash" },
            embedding: {
              provider: "gemini",
              model: "gemini-embedding-001",
              similarity_threshold: 0.85,
            },
          },
        };
      },
    };
  };

  const result = await fetchConfig();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config");
  assert.equal(calls[0].options.method, "GET");
  assert.ok(!calls[0].url.includes("reveal_keys"));
  assert.equal(result.llm.default_provider, "gemini");
  // Masked placeholder from the backend is passed through untouched — the
  // popup must never see (or request) the raw credential.
  assert.equal(result.bilibili.cookie, "SESS****masked");
  assert.equal(result.llm.embedding.provider, "gemini");
  assert.equal(result.llm.embedding.model, "gemini-embedding-001");
  assert.equal(result.llm.embedding.similarity_threshold, 0.85);
});

test("popup-api source never requests revealed credentials or legacy saved routes", () => {
  const source = readFileSync(resolve("popup/popup-api.ts"), "utf8");

  // Credential-revealing query param must not appear anywhere in popup code.
  assert.doesNotMatch(source, /reveal_keys=true/);
  // Legacy Bilibili-only saved routes must not be called by the popup —
  // the platform-neutral /saved/{watch_later|favorite} API is the only path.
  assert.doesNotMatch(source, /requestJson\(["'`]\/watch-later/);
  assert.doesNotMatch(source, /requestJson\(["'`]\/favorites/);
});

test("popup-api drops legacy bilibili saved exports, keeps platform-neutral saved API", () => {
  for (const name of [
    "addToWatchLater",
    "removeFromWatchLater",
    "watchLaterStatus",
    "fetchWatchLater",
    "addToFavorite",
    "removeFromFavorite",
    "favoriteStatus",
    "fetchFavorites",
  ]) {
    assert.equal(
      typeof (popupApi as any)[name],
      "undefined",
      `legacy export ${name} should be removed`,
    );
  }
  for (const name of [
    "saveItem",
    "removeSavedItem",
    "savedItemStatus",
    "fetchSavedItems",
    "syncSavedItems",
    "pollSavedSyncTask",
  ]) {
    assert.equal(
      typeof (popupApi as any)[name],
      "function",
      `canonical saved helper ${name} should remain`,
    );
  }
});

test("fetchConfig caches successful config snapshots in chrome storage", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  const writes: Array<Record<string, unknown>> = [];
  const storage: Record<string, unknown> = {};
  (globalThis as { chrome?: unknown }).chrome = {
    storage: {
      local: {
        get(key: string, callback: (items: Record<string, unknown>) => void) {
          callback({ [key]: storage[key] });
        },
        set(items: Record<string, unknown>, callback: () => void) {
          writes.push(items);
          Object.assign(storage, items);
          callback();
        },
      },
    },
  };
  const calls: Array<{ url: string }> = [];
  globalThis.fetch = async (url: any) => {
    calls.push({ url });
    return {
      ok: true,
      async json() {
        return {
          language: "zh",
          llm: {
            default_provider: "openai",
            openai: { api_key: "" },
          },
          bilibili: { cookie: "SESS****ED" },
        };
      },
    } as Response;
  };

  try {
    const result = await fetchConfig();
    const cached = await readCachedConfigSnapshot();

    assert.equal(result.llm.default_provider, "openai");
    assert.equal(writes.length, 1);
    assert.ok(writes[0]["openbiliclaw.config_cache"]);
    // The cached snapshot carries the masked endpoint payload verbatim, so a
    // raw credential can never end up in chrome.storage.
    assert.equal(cached?.config.bilibili.cookie, "SESS****ED");
    assert.equal(cached?.config.llm.openai.api_key, "");
    assert.ok(!JSON.stringify(cached).includes("reveal_keys"));
    assert.ok(calls.every((call) => !call.url.includes("reveal_keys")));
    assert.match(cached?.cached_at ?? "", /^\d{4}-\d{2}-\d{2}T/);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});

test("model-config api reads safe snapshots and descriptor groups without reveal_keys", async () => {
  for (const name of ["fetchModelConfig", "fetchModelConnectionTypes"]) {
    assert.equal(typeof (popupApi as any)[name], "function", `${name} should be exported`);
  }
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = (async (url: string, options: any) => {
    calls.push({ url, options });
    if (url.endsWith("/model-connection-types")) {
      return {
        ok: true,
        status: 200,
        async json() {
          return { connection_types: [], groups: [] };
        },
      };
    }
    return {
      ok: true,
      status: 200,
      async json() {
        return modelSnapshotFixture(["a"]);
      },
    };
  }) as unknown as typeof fetch;

  const modelSnapshot = await (popupApi as any).fetchModelConfig();
  const descriptors = await (popupApi as any).fetchModelConnectionTypes();

  assert.equal(modelSnapshot.revision, "revision-a");
  assert.deepEqual(descriptors.groups, []);
  assert.deepEqual(
    calls.map((call) => call.url),
    ["http://127.0.0.1:8420/api/model-config", "http://127.0.0.1:8420/api/model-connection-types"],
  );
  assert.ok(calls.every((call) => !call.url.includes("reveal_keys")));
  assert.ok(calls.every((call) => call.options.method === "GET"));
});

test("model-config api sends one revisioned route update through its dedicated endpoint", async () => {
  assert.equal(typeof (popupApi as any).updateModelConfig, "function");
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = (async (url: string, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { snapshot: modelSnapshotFixture(["a"], "revision-b") };
      },
    };
  }) as unknown as typeof fetch;
  const payload = {
    revision: "revision-a",
    models: modelSnapshotFixture(["a"]).models,
    migration_resolutions: {},
  };

  await (popupApi as any).updateModelConfig(payload);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/model-config");
  assert.equal(calls[0].options.method, "PUT");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), payload);
  assert.equal("llm" in JSON.parse(calls[0].options.body), false);
});

test("model-config api probes one exact draft and preserves 409 conflict details", async () => {
  assert.equal(typeof (popupApi as any).probeModelConnection, "function");
  assert.equal((popupApi as any).MODEL_CONFIG_PROBE_TIMEOUT_MS, 60_000);
  const calls: Array<{ url: string; options: any }> = [];
  const exact = {
    kind: "embedding",
    revision: "revision-a",
    provider: {
      id: "embedding-a",
      name: "Embedding A",
      type: "ollama",
      preset: "",
      base_url: "http://127.0.0.1:11434/v1",
      credential: { action: "clear" },
    },
    settings: {
      model: "bge-m3",
      output_dimensionality: 1024,
      similarity_threshold: 0.82,
      multimodal_enabled: false,
    },
  };
  globalThis.fetch = (async (url: string, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { ok: true, connection_id: "embedding-a", observed_dimension: 1024 };
      },
    };
  }) as unknown as typeof fetch;

  await (popupApi as any).probeModelConnection(exact);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/model-config/probe");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), exact);

  const details = {
    error: "revision_conflict",
    latest: modelSnapshotFixture(["remote"], "revision-b"),
  };
  globalThis.fetch = (async () => ({
    ok: false,
    status: 409,
    async json() {
      return details;
    },
  })) as unknown as typeof fetch;
  await assert.rejects(
    () => (popupApi as any).updateModelConfig({ revision: "revision-a" }),
    (error: any) => {
      assert.equal(error.status, 409);
      assert.deepEqual(error.details, details);
      return true;
    },
  );
});

test("cacheConfigSnapshot no-ops when chrome storage is unavailable", async () => {
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  delete (globalThis as { chrome?: unknown }).chrome;

  try {
    const snapshot = await cacheConfigSnapshot({ language: "zh" });
    assert.equal(snapshot, null);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});

test("fetchSourceShareSuggestion loads source-share recommendation", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          event_counts: { bilibili: 9, youtube: 4 },
          enabled_sources: { bilibili: true, youtube: true },
          suggested_shares: { bilibili: 8, youtube: 5 },
        };
      },
    };
  };

  const result = await fetchSourceShareSuggestion();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config/source-share-suggestion");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(result.suggested_shares.youtube, 5);
});

test("fetchSourceShareSuggestion posts current settings overrides when provided", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          event_counts: { bilibili: 9, youtube: 4 },
          enabled_sources: { bilibili: true, youtube: true },
          suggested_shares: { bilibili: 6, youtube: 4 },
        };
      },
    };
  };

  const result = await fetchSourceShareSuggestion({
    enabled_sources: {
      bilibili: true,
      xiaohongshu: false,
      douyin: false,
      youtube: true,
    },
    configured_shares: {
      bilibili: 6,
      xiaohongshu: 2,
      douyin: 1,
      youtube: 2,
    },
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config/source-share-suggestion");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    enabled_sources: {
      bilibili: true,
      xiaohongshu: false,
      douyin: false,
      youtube: true,
    },
    configured_shares: {
      bilibili: 6,
      xiaohongshu: 2,
      douyin: 1,
      youtube: 2,
    },
  });
  assert.equal(result.suggested_shares.youtube, 4);
});

test("probeConfigService posts no-write config probe payload", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          ok: true,
          kind: "llm",
          provider: "openai",
          message: "LLM provider is available.",
        };
      },
    };
  };

  const result = await probeConfigService("llm", {
    llm: { default_provider: "openai", openai: { api_key: "sk-test" } },
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config/probe-service");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    kind: "llm",
    config: {
      llm: { default_provider: "openai", openai: { api_key: "sk-test" } },
    },
  });
  assert.equal(result.ok, true);
  assert.equal(result.provider, "openai");
});

test("updateConfig sends only unrelated general settings through the legacy config endpoint", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          ok: true,
          config: { language: "zh", scheduler: { enabled: true } },
          message: "配置已保存。",
          reloaded: true,
        };
      },
    };
  };

  const payload = { language: "zh", scheduler: { enabled: true } };

  const result = await updateConfig(payload);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/config");
  assert.equal(calls[0].options.method, "PUT");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");

  const sentBody = JSON.parse(calls[0].options.body);
  assert.equal(sentBody.language, "zh");
  assert.deepEqual(sentBody.scheduler, { enabled: true });
  assert.equal("llm" in sentBody, false);

  assert.equal(result.ok, true);
  assert.equal(result.reloaded, true);
});

test("updateConfig preserves structured details from validation errors", async () => {
  const details = {
    ok: false,
    reloaded: false,
    rollback_applied: false,
    config: {
      issues: [
        {
          field: "llm",
          message: "LLM registry would fail to build",
          severity: "blocking",
        },
      ],
    },
    message: "配置校验失败，未写入 config.toml。",
  };
  globalThis.fetch = async () => ({
    ok: false,
    status: 400,
    async json() {
      return details;
    },
  });

  await assert.rejects(
    () => updateConfig({ reset_fields: ["llm.openai.api_key"] }),
    (error: any) => {
      assert.equal(error.message, "/config request failed: 400");
      assert.equal(error.status, 400);
      assert.deepEqual(error.details, details);
      return true;
    },
  );
});

test("startChatTurn posts durable chat turn metadata", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return {
          turn_id: "turn-abc",
          session: "popup",
          scope: "delight",
          subject_id: "BV1DL",
          subject_title: "复杂系统入门",
          message: "我想聊聊这条",
          reply: "",
          status: "pending",
          error: "",
          created_at: "2026-05-15 10:00:00",
          updated_at: "2026-05-15 10:00:00",
        };
      },
    };
  };

  const result = await startChatTurn({
    turnId: "turn-abc",
    session: "popup",
    scope: "delight",
    subjectId: "BV1DL",
    subjectTitle: "复杂系统入门",
    message: "我想聊聊这条",
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/chat/turns");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    turn_id: "turn-abc",
    session: "popup",
    scope: "delight",
    subject_id: "BV1DL",
    subject_title: "复杂系统入门",
    message: "我想聊聊这条",
  });
  assert.equal(result.status, "pending");
});

test("fetchChatTurn and fetchChatTurns read durable chat state", async () => {
  const calls: Array<{ url: string; options: any }> = [];
  globalThis.fetch = async (url: any, options: any) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        if (String(url).endsWith("/api/chat/turns/turn-abc")) {
          return {
            turn_id: "turn-abc",
            session: "popup",
            scope: "chat",
            message: "你好",
            reply: "你好，我在。",
            status: "completed",
          };
        }
        return {
          items: [
            {
              turn_id: "turn-abc",
              session: "popup",
              scope: "chat",
              message: "你好",
              reply: "你好，我在。",
              status: "completed",
            },
          ],
        };
      },
    };
  };

  const turn = await fetchChatTurn("turn-abc");
  const history = await fetchChatTurns({ session: "popup", scope: "chat", limit: 10 });

  assert.equal(calls[0].url, "http://127.0.0.1:8420/api/chat/turns/turn-abc");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(
    calls[1].url,
    "http://127.0.0.1:8420/api/chat/turns?session=popup&scope=chat&limit=10",
  );
  assert.equal(calls[1].options.method, "GET");
  assert.equal(turn.reply, "你好，我在。");
  assert.equal(history.items[0].turn_id, "turn-abc");
});

test("popup-api requests honor configured backend host and port from chrome.storage.local", async () => {
  // Reset module cache so the previous tests' default-port resolution
  // doesn't shadow the stubbed chrome.storage value.
  __resetBackendEndpointForTests();
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    storage: {
      local: {
        get(_key: string, callback: (items: Record<string, unknown>) => void) {
          callback({
            popup_backend_endpoint: {
              host: "192.168.1.100",
              port: 19090,
              basePath: "/api",
            },
          });
        },
      },
    },
  };

  const calls: Array<{ url: string; options: { method?: string } }> = [];
  globalThis.fetch = (async (url: string, options: { method?: string }) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { language: "zh" };
      },
    };
  }) as unknown as typeof fetch;

  try {
    await fetchConfig();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "http://192.168.1.100:19090/api/config");
    assert.ok(!calls[0].url.includes("reveal_keys"));
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
    __resetBackendEndpointForTests();
  }
});

test("requestJson aborts fetch after timeoutMs", async () => {
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    return new Promise((_resolve, reject) => {
      if (!options.signal) {
        setTimeout(() => reject(new Error("missing abort signal")), 100);
        return;
      }
      options.signal.addEventListener("abort", () => {
        reject(options.signal?.reason ?? new DOMException("Aborted", "AbortError"));
      });
    });
  }) as unknown as typeof fetch;

  await assert.rejects(
    requestJson("/slow", { method: "GET", timeoutMs: 20 }),
    (error: unknown) => error instanceof Error && error.name === "AbortError",
  );
});

test("requestJson without timeout preserves no-signal fetch behavior", async () => {
  const calls: Array<{ signal?: AbortSignal }> = [];
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    calls.push(options);
    return {
      ok: true,
      async json() {
        return { ok: true };
      },
    };
  }) as unknown as typeof fetch;

  const result = await requestJson("/fast", { method: "GET" });

  assert.deepEqual(result, { ok: true });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].signal, undefined);
});

test("requestJson preserves caller abort reason before timeout fires", async () => {
  const controller = new AbortController();
  const reason = new DOMException("caller cancelled", "AbortError");
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    return new Promise((_resolve, reject) => {
      options.signal?.addEventListener("abort", () => {
        reject(options.signal?.reason ?? new DOMException("Aborted", "AbortError"));
      });
      queueMicrotask(() => controller.abort(reason));
    });
  }) as unknown as typeof fetch;

  await assert.rejects(
    requestJson("/caller-abort", {
      method: "GET",
      signal: controller.signal,
      timeoutMs: 200,
    }),
    (error: unknown) => error === reason,
  );
});

test("updateConfig uses the shared 60s config PUT timeout", async () => {
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  const delays: number[] = [];
  globalThis.setTimeout = ((callback: TimerHandler, delay?: number) => {
    delays.push(Number(delay));
    queueMicrotask(() => {
      if (typeof callback === "function") callback();
    });
    return 1 as unknown as ReturnType<typeof setTimeout>;
  }) as typeof setTimeout;
  globalThis.clearTimeout = ((_id?: unknown) => undefined) as typeof clearTimeout;
  globalThis.fetch = (async (_url: string, options: { signal?: AbortSignal }) => {
    return new Promise((_resolve, reject) => {
      options.signal?.addEventListener("abort", () => {
        reject(options.signal?.reason ?? new DOMException("Aborted", "AbortError"));
      });
    });
  }) as unknown as typeof fetch;

  try {
    await assert.rejects(
      updateConfig({ language: "zh" }),
      (error: unknown) => error instanceof Error && error.name === "AbortError",
    );
    assert.deepEqual(delays, [60_000]);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});
