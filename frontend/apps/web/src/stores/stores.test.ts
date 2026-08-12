import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WebApi } from "../services/api";
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
function api(overrides: Partial<WebApi> = {}): WebApi {
  return {
    listSources: async () => [],
    recommendations: async () => ({ items: [] }),
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
    events: () => emptyStream(),
    ...overrides,
  };
}
beforeEach(() => setActivePinia(createPinia()));

describe("durable concern stores", () => {
  it("exposes loading-empty-success-error states", async () => {
    const recommendations = useRecommendationsStore();
    await recommendations.load(api());
    expect(recommendations.phase).toBe("empty");
    await recommendations.load(
      api({
        recommendations: async () => ({
          items: [
            {
              candidate_id: "c",
              recommendation_id: "r",
              rank: 1,
              score: 1,
              seed: 1,
              selected_at: "2030-01-01T00:00:00Z",
              contributions: [],
            },
          ],
        }),
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
    expect(content.phase).toBe("empty");
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
    expect(content.phase).toBe("success");
  });

  it("loads runtime health and conversation history", async () => {
    const runtime = useRuntimeStore();
    await runtime.load(api());
    expect(runtime.health?.health.status).toBe("healthy");
    const assistant = useAssistantStore();
    await assistant.load(api(), "conv", "device");
    expect(assistant.phase).toBe("empty");
  });

  it("passes AbortSignals and cancels the previous server read", async () => {
    const signals: AbortSignal[] = [];
    const pending = api({
      recommendations: async (signal) => {
        if (signal !== undefined) signals.push(signal);
        return { items: [] };
      },
    });
    const store = useRecommendationsStore();
    await store.load(pending);
    await store.load(pending);
    expect(signals).toHaveLength(2);
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
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

  it("stores bounded assistant results without optimistic messages", async () => {
    const store = useAssistantStore();
    await store.send(api(), "conv", "device", "hello");
    expect(store.latest?.output).toEqual({ kind: "message", text: "hello" });
    expect(store.phase).toBe("success");
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

  it("owns one reconnecting event stream with bounded backoff", async () => {
    const attempts: number[] = [];
    let calls = 0;
    const web = api({
      events: async function* () {
        calls += 1;
        if (calls === 1) throw new Error("lost");
        yield { kind: "job", event_id: 1, component_id: "core", status: "ok" };
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
    expect(attempts).toEqual([100, 100]);
    expect(store.events).toHaveLength(1);
  });
});
