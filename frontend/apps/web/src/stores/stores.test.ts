import { ApiError } from "@openbiliclaw/api-client";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RecommendationPage, WebApi } from "../services/api";
import { useRecommendationsStore } from "./recommendations";
import { useSourcesStore } from "./sources";
import { useProfileStore } from "./profile";
import { useAssistantStore } from "./assistant";
import { usePreferencesStore } from "./preferences";
import { useRuntimeStore, type Delay } from "./runtime";
import { useSessionStore } from "./session";
import { useContentStore } from "./content";

const emptyStream = async function* () {
  yield* [] as never[];
};
function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function api(overrides: Partial<WebApi> = {}): WebApi {
  return {
    listSources: async () => [],
    connectSource: async (body) => ({
      availability_refreshed: true,
      recoverable: false,
      status: {
        provider_id: body.provider_id,
        account_id: null,
        state: "connected",
      },
    }),
    recommendations: async () => ({ items: [] }),
    feedback: async () => ({
      result: {
        feedback_id: "feedback_11111111111111111111111111111111",
        observation_id: "obs_11111111111111111111111111111111",
        inserted: true,
      },
    }),
    profile: async () => ({
      profile: { version: 1, preference_summary: [], insights: [] },
    }),
    editProfile: async () => ({
      observation_id: "obs",
      profile: {
        profile_id: "default",
        revision: 1,
        updated_at: "2030-01-01T00:00:00Z",
        claims: [],
        overrides: [],
      },
    }),
    assistantTurn: async () => ({ output: { kind: "message", text: "hello" } }),
    conversation: async () => ({
      conversation: {
        conversation_id: "conv",
        created_at: "2030-01-01T00:00:00Z",
        updated_at: "2030-01-01T00:00:00Z",
        retention_days: 30,
        scope: { local_user_id: "u", device_id: "d" },
      },
      messages: [],
    }),
    runtimeHealth: async () => ({
      health: {
        component_id: "core",
        status: "healthy",
        checked_at: "2030-01-01T00:00:00Z",
        jobs: [],
      },
    }),
    search: async () => ({ items: [] }),
    content: async () => {
      throw new Error("unused");
    },
    modelCatalog: async () => ({ providers: [] }),
    currentModel: async () => ({
      current: {
        model: {
          provider: "openai",
          model_name: "",
          endpoint: null,
          secret_configured: false,
          protocol: null,
          capabilities: null,
        },
        embedding: {
          provider: "openai",
          model_name: "",
          endpoint: null,
          secret_configured: false,
        },
      },
      reloaded: false,
      restart_required: false,
    }),
    updateModel: async () => {
      throw new Error("unused");
    },
    events: () => emptyStream(),
    ...overrides,
  };
}
const feedItem = {
  shown_id: "shown_11111111111111111111111111111111",
  ref: {
    provider_id: { value: "demo" },
    content_kind: { value: "video" },
    provider_content_id: "1",
    canonical_url: "https://example.test/1",
  },
  card: {
    ref: {
      provider_id: { value: "demo" },
      content_kind: { value: "video" },
      provider_content_id: "1",
      canonical_url: "https://example.test/1",
    },
    title: "One",
    summary: "Summary",
    badge: null,
    image_url: null,
    source_timestamp: "2030-01-01T00:00:00Z",
    provenance: {
      ref: {
        provider_id: { value: "demo" },
        content_kind: { value: "video" },
        provider_content_id: "1",
        canonical_url: "https://example.test/1",
      },
      native_schema_version: 1,
      projected_at: "2030-01-01T00:00:00Z",
    },
  },
  reason: "Recommended for relevance and freshness.",
  selection: {
    candidate_id: "c",
    recommendation_id: "r",
    rank: 1,
    score: 1,
    seed: 1,
    selected_at: "2030-01-01T00:00:00Z",
    contributions: [],
  },
} satisfies RecommendationPage["items"][number];
beforeEach(() => setActivePinia(createPinia()));

describe("durable concern stores", () => {
  it("exposes loading-empty-success-error states", async () => {
    const recommendations = useRecommendationsStore();
    await recommendations.load(api());
    expect(recommendations.phase).toBe("empty");
    await recommendations.load(
      api({
        recommendations: async () => ({ items: [feedItem] }),
      }),
    );
    expect(recommendations.phase).toBe("success");
    await recommendations.load(
      api({
        recommendations: async () => {
          throw new Error("offline");
        },
      }),
    );
    expect(recommendations.phase).toBe("error");
    expect(recommendations.error).toBe("offline");
    const sources = useSourcesStore();
    await sources.load(api());
    expect(sources.phase).toBe("empty");
  });

  it("submits server-authoritative recommendation feedback and exposes typed expiry", async () => {
    const feedback = vi.fn(api().feedback);
    const store = useRecommendationsStore();
    await store.load(
      api({ recommendations: async () => ({ items: [feedItem] }) }),
    );
    const card = store.cards[0];
    if (card === undefined) throw new Error("expected recommendation card");
    await store.like(api({ feedback }), card);
    expect(feedback).toHaveBeenCalledWith(
      {
        idempotency_key: `${feedItem.shown_id}:liked`,
        shown_id: feedItem.shown_id,
        content_ref: feedItem.ref,
        kind: "liked",
      },
      expect.any(AbortSignal),
    );
    expect(store.feedbackState[feedItem.shown_id]).toBe("liked");

    const expired = new Error("Request failed with status 404");
    Object.assign(expired, { status: 404 });
    await store.dismiss(
      api({
        feedback: async () => {
          throw expired;
        },
      }),
      card,
    );
    expect(store.feedbackError[feedItem.shown_id]).toBe(
      "This recommendation expired. Refresh the feed and try again.",
    );
  });

  it("tracks session/auth without mirroring backend state", () => {
    const session = useSessionStore();
    session.establish("device-2", true);
    expect(session.deviceId).toBe("device-2");
    expect(session.status).toBe("authenticated");
  });

  it("covers source success and failure actions", async () => {
    const sources = useSourcesStore();
    await sources.load(
      api({
        listSources: async () => [
          { provider_id: "demo", account_id: null, state: "connected" },
        ],
      }),
    );
    expect(sources.phase).toBe("success");
    await sources.load(
      api({
        listSources: async () => {
          throw new Error("denied");
        },
      }),
    );
    expect(sources.phase).toBe("error");
  });

  it("covers content search and detail actions", async () => {
    const content = useContentStore();
    await content.search(api(), "demo", "query");
    expect(content.searchPhase).toBe("empty");
    await content.fetchDetail(
      api({
        content: async () => ({
          content: {
            ref: {
              provider_id: { value: "demo" },
              content_kind: { value: "video" },
              provider_content_id: "1",
              canonical_url: "https://example.test/1",
            },
            schema_version: 1,
            payload: {},
          },
        }),
      }),
      "ref",
    );
    expect(content.detailPhase).toBe("success");
  });

  it("keeps content search and detail errors isolated", async () => {
    const content = useContentStore();
    await content.fetchDetail(
      api({ content: async () => Promise.reject(new Error("detail failed")) }),
      "ref",
    );
    expect(content.detailError).toBe("detail failed");
    expect(content.searchError).toBeUndefined();
    await content.search(api(), "demo", "query");
    expect(content.searchPhase).toBe("empty");
    expect(content.detailError).toBe("detail failed");
  });

  it("loads runtime health and conversation history", async () => {
    const runtime = useRuntimeStore();
    await runtime.load(api());
    expect(runtime.health?.health.status).toBe("healthy");
    const assistant = useAssistantStore();
    await assistant.load(api(), "conv", "device");
    expect(assistant.phase).toBe("empty");
  });

  it("cancels concurrent reads and rejects stale commits", async () => {
    const first = deferred<{ items: [] }>();
    const second = deferred<{ items: [] }>();
    const signals: AbortSignal[] = [];
    let calls = 0;
    const pending = api({
      recommendations: (signal) => {
        if (signal !== undefined) signals.push(signal);
        calls += 1;
        return calls === 1 ? first.promise : second.promise;
      },
    });
    const store = useRecommendationsStore();
    const oldLoad = store.load(pending);
    const newLoad = store.load(pending);
    expect(store.phase).toBe("loading");
    first.resolve({ items: [] });
    await oldLoad;
    expect(store.phase).toBe("loading");
    second.resolve({ items: [] });
    await newLoad;
    expect(store.phase).toBe("empty");
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
  });

  it("ignores cancellation in content, assistant, profile, sources, and runtime stores", async () => {
    const aborted = async (): Promise<never> => {
      throw new DOMException("Aborted", "AbortError");
    };
    const content = useContentStore();
    await content.search(api({ search: aborted }), "demo", "q");
    expect(content.searchPhase).toBe("loading");
    const assistant = useAssistantStore();
    await assistant.send(api({ assistantTurn: aborted }), "conv", "d", "q");
    expect(assistant.phase).toBe("loading");
    const profile = useProfileStore();
    await profile.load(api({ profile: aborted }));
    expect(profile.phase).toBe("loading");
    const sources = useSourcesStore();
    await sources.load(api({ listSources: aborted }));
    expect(sources.phase).toBe("loading");
    const runtime = useRuntimeStore();
    await runtime.load(api({ runtimeHealth: aborted }));
    expect(runtime.phase).toBe("loading");
  });

  it("keeps profile server-authoritative after edits", async () => {
    const profile = useProfileStore();
    const load = vi.fn(async () => ({
      profile: { version: 1, preference_summary: ["server"], insights: [] },
    }));
    await profile.edit(api({ profile: load }), {
      account_id: "u",
      claim_id: "claim_00000000000000000000000000000000",
      idempotency_key: "edit:0001",
      operation: "set",
      profile_id: "default",
      value: "server",
    });
    expect(load).toHaveBeenCalled();
    expect(profile.result?.profile.preference_summary).toEqual(["server"]);
  });

  it("stores the submitted assistant message with its response", async () => {
    const store = useAssistantStore();
    await store.send(api(), "conv", "device", "hello");
    expect(store.latestUserText).toBe("hello");
    expect(store.latest?.output).toEqual({ kind: "message", text: "hello" });
    expect(store.phase).toBe("success");
  });

  it("clears a stale stored assistant conversation after a 404", async () => {
    const missing = new Error("route not found");
    Object.assign(missing, { status: 404 });
    const storage = { removeItem: vi.fn() };
    const found = await useAssistantStore().load(
      api({ conversation: async () => Promise.reject(missing) }),
      "conv_missing",
      "device",
      storage,
    );
    expect(found).toBe(false);
    expect(storage.removeItem).toHaveBeenCalledWith("obc-conversation-id");
    expect(useAssistantStore().phase).toBe("empty");
    expect(useAssistantStore().error).toBeUndefined();
  });

  it("hydrates and persists host-local preferences", () => {
    const store = usePreferencesStore();
    const values = new Map([
      ["obc-density", "compact"],
      ["obc-reduced-motion", "true"],
    ]);
    store.hydrate({ getItem: (key) => values.get(key) ?? null });
    expect(store.density).toBe("compact");
    expect(store.reducedMotion).toBe(true);
    const setItem = vi.fn();
    const stop = store.persist({ setItem });
    expect(setItem).toHaveBeenCalled();
    stop();
  });

  it("replays after the last cursor, deduplicates, caps events, and disconnects", async () => {
    const attempts: number[] = [];
    const cursors: Array<number | undefined> = [];
    let calls = 0;
    const web = api({
      events: async function* (after) {
        cursors.push(after);
        calls += 1;
        if (calls === 1) {
          for (let eventId = 1; eventId <= 51; eventId += 1) {
            yield {
              kind: "job",
              event_id: eventId,
              component_id: "core",
              status: "ok",
            };
          }
          throw new Error("lost");
        }
        yield { kind: "job", event_id: 51, component_id: "core", status: "ok" };
      },
    });
    const delay: Delay = async (ms) => {
      attempts.push(ms);
      if (attempts.length > 1) throw new DOMException("Aborted", "AbortError");
    };
    const store = useRuntimeStore();
    const first = store.connect(web, delay);
    void store.connect(web, delay);
    await first;
    expect(calls).toBe(2);
    expect(cursors).toEqual([undefined, 51]);
    expect(attempts).toEqual([100, 500]);
    expect(store.events).toHaveLength(50);
    expect(store.events.filter((event) => event.event_id === 51)).toHaveLength(
      1,
    );
    const endless = store.connect(
      api({
        events: (_after, signal) => ({
          async *[Symbol.asyncIterator]() {
            yield* [] as never[];
            if (signal === undefined || signal.aborted) return;
            await new Promise<void>((resolve) =>
              signal.addEventListener("abort", () => resolve(), { once: true }),
            );
          },
        }),
      }),
      delay,
    );
    store.disconnect();
    await endless;
    expect(store.streamConnected).toBe(false);
  });

  it("uses the server SSE retry hint before local reconnect backoff", async () => {
    const waits: number[] = [];
    const delay: Delay = async (milliseconds) => {
      waits.push(milliseconds);
      throw new DOMException("Aborted", "AbortError");
    };
    await useRuntimeStore().connect(
      api({
        events: () => ({
          async *[Symbol.asyncIterator]() {
            yield* [] as never[];
            throw new ApiError(
              "network",
              "Event stream disconnected",
              undefined,
              3_000,
            );
          },
        }),
      }),
      delay,
    );
    expect(waits).toEqual([3_000]);
  });

  it("caps reconnect backoff at two seconds", async () => {
    const waits: number[] = [];
    const delay: Delay = async (ms) => {
      waits.push(ms);
      if (waits.length === 6) throw new DOMException("Aborted", "AbortError");
    };
    await useRuntimeStore().connect(
      api({
        events: () => ({
          async *[Symbol.asyncIterator]() {
            yield* [] as never[];
            throw new Error("lost");
          },
        }),
      }),
      delay,
    );
    expect(waits).toEqual([100, 500, 1_000, 2_000, 2_000, 2_000]);
  });
});
