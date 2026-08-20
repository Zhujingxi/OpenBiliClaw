import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import {
  EMPTY_SOURCE_INVENTORY,
  type ConversationResponse,
  type ProfileResponse,
  type RuntimeResponse,
  type SearchResponse,
  type SourceListResponse,
  type SourceStatus,
  type WebApi,
} from "../services/api";
import { useAssistantStore } from "./assistant";
import { useContentStore } from "./content";
import { useProfileStore } from "./profile";
import { useRuntimeStore } from "./runtime";
import { useSourcesStore } from "./sources";

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
function sourceList(items: SourceStatus[] = []): SourceListResponse {
  return { items, inventory: EMPTY_SOURCE_INVENTORY };
}
function api(overrides: Partial<WebApi>): WebApi {
  const unused = async (): Promise<never> => {
    throw new Error("unused");
  };
  const unusedStream = async function* () {
    yield* [] as never[];
    throw new Error("unused");
  };
  return {
    login: unused,
    listSources: unused,
    connectSource: unused,
    recommendations: unused,
    refreshRecommendations: unused,
    feedback: unused,
    profile: unused,
    editProfile: unused,
    assistantTurnStream: unusedStream,
    conversation: unused,
    runtimeHealth: unused,
    search: unused,
    content: unused,
    modelCatalog: unused,
    currentModel: unused,
    updateModel: unused,
    events: async function* () {
      yield* [] as never[];
    },
    ...overrides,
  };
}
const profileEmpty: ProfileResponse = {
  profile: { version: 1, preference_summary: [], insights: [] },
};
const profileSuccess: ProfileResponse = {
  profile: { version: 1, preference_summary: ["science"], insights: [] },
};
const conversationEmpty: ConversationResponse = {
  conversation: {
    conversation_id: "conv",
    created_at: "2030-01-01T00:00:00Z",
    updated_at: "2030-01-01T00:00:00Z",
    retention_days: 30,
    scope: { local_user_id: "u", device_id: "d" },
  },
  messages: [],
};
const conversationSuccess: ConversationResponse = {
  ...conversationEmpty,
  messages: [
    {
      message_id: "m",
      idempotency_key: "k",
      role: "assistant",
      content: "hello",
      created_at: "2030-01-01T00:00:00Z",
      references: [],
      tool_calls: [],
      user_correction: false,
    },
  ],
};
const source: SourceStatus = {
  provider_id: "demo",
  account_id: null,
  state: "connected",
};
const health: RuntimeResponse = {
  health: {
    component_id: "core",
    status: "healthy",
    checked_at: "2030-01-01T00:00:00Z",
    jobs: [],
  },
};
const result: SearchResponse["items"][number] = {
  ref: {
    provider_id: { value: "demo" },
    content_kind: { value: "video" },
    provider_content_id: "1",
    canonical_url: "https://example.test/1",
  },
  title: "One",
  summary: "Summary",
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
};
beforeEach(() => setActivePinia(createPinia()));

describe("store state matrices", () => {
  it("covers content loading, success, empty, error, and stale completion", async () => {
    const store = useContentStore();
    await store.search(api({ search: async () => ({ items: [] }) }), "p", "q");
    expect(store.searchPhase).toBe("empty");
    await store.search(
      api({ search: async () => ({ items: [result] }) }),
      "p",
      "q",
    );
    expect(store.searchPhase).toBe("success");
    await store.search(
      api({ search: async () => Promise.reject(new Error("bad")) }),
      "p",
      "q",
    );
    expect(store.searchPhase).toBe("error");
    const old = deferred<SearchResponse>();
    const fresh = deferred<SearchResponse>();
    let calls = 0;
    const web = api({
      search: () => (++calls === 1 ? old.promise : fresh.promise),
    });
    const first = store.search(web, "p", "old");
    const second = store.search(web, "p", "new");
    expect(store.searchPhase).toBe("loading");
    old.resolve({ items: [result] });
    await first;
    expect(store.searchPhase).toBe("loading");
    fresh.resolve({ items: [] });
    await second;
    expect(store.searchPhase).toBe("empty");
  });

  it("covers profile loading, success, empty, error, and stale completion", async () => {
    const store = useProfileStore();
    await store.load(api({ profile: async () => profileEmpty }));
    expect(store.phase).toBe("empty");
    await store.load(api({ profile: async () => profileSuccess }));
    expect(store.phase).toBe("success");
    await store.load(
      api({ profile: async () => Promise.reject(new Error("bad")) }),
    );
    expect(store.phase).toBe("error");
    const old = deferred<ProfileResponse>();
    const fresh = deferred<ProfileResponse>();
    let calls = 0;
    const web = api({
      profile: () => (++calls === 1 ? old.promise : fresh.promise),
    });
    const first = store.load(web);
    const second = store.load(web);
    expect(store.phase).toBe("loading");
    old.resolve(profileSuccess);
    await first;
    expect(store.phase).toBe("loading");
    fresh.resolve(profileEmpty);
    await second;
    expect(store.phase).toBe("empty");
  });

  it("covers assistant loading, success, empty, error, and stale completion", async () => {
    const store = useAssistantStore();
    await store.load(
      api({ conversation: async () => conversationEmpty }),
      "c",
      "d",
    );
    expect(store.phase).toBe("empty");
    await store.load(
      api({ conversation: async () => conversationSuccess }),
      "c",
      "d",
    );
    expect(store.phase).toBe("success");
    await store.load(
      api({ conversation: async () => Promise.reject(new Error("bad")) }),
      "c",
      "d",
    );
    expect(store.phase).toBe("error");
    const old = deferred<ConversationResponse>();
    const fresh = deferred<ConversationResponse>();
    let calls = 0;
    const web = api({
      conversation: () => (++calls === 1 ? old.promise : fresh.promise),
    });
    const first = store.load(web, "c", "d");
    const second = store.load(web, "c", "d");
    expect(store.phase).toBe("loading");
    old.resolve(conversationSuccess);
    await first;
    expect(store.phase).toBe("loading");
    fresh.resolve(conversationEmpty);
    await second;
    expect(store.phase).toBe("empty");
  });

  it("covers sources loading, success, empty, error, and stale completion", async () => {
    const store = useSourcesStore();
    await store.load(api({ listSources: async () => sourceList() }));
    expect(store.phase).toBe("empty");
    await store.load(api({ listSources: async () => sourceList([source]) }));
    expect(store.phase).toBe("success");
    await store.load(
      api({ listSources: async () => Promise.reject(new Error("bad")) }),
    );
    expect(store.phase).toBe("error");
    const old = deferred<SourceListResponse>();
    const fresh = deferred<SourceListResponse>();
    let calls = 0;
    const web = api({
      listSources: () => (++calls === 1 ? old.promise : fresh.promise),
    });
    const first = store.load(web);
    const second = store.load(web);
    expect(store.phase).toBe("loading");
    old.resolve(sourceList([source]));
    await first;
    expect(store.phase).toBe("loading");
    fresh.resolve(sourceList());
    await second;
    expect(store.phase).toBe("empty");
  });

  it("covers runtime loading, success, error, and stale completion", async () => {
    const store = useRuntimeStore();
    await store.load(api({ runtimeHealth: async () => health }));
    expect(store.phase).toBe("success");
    await store.load(
      api({ runtimeHealth: async () => Promise.reject(new Error("bad")) }),
    );
    expect(store.phase).toBe("error");
    const old = deferred<RuntimeResponse>();
    const fresh = deferred<RuntimeResponse>();
    let calls = 0;
    const web = api({
      runtimeHealth: () => (++calls === 1 ? old.promise : fresh.promise),
    });
    const first = store.load(web);
    const second = store.load(web);
    expect(store.phase).toBe("loading");
    old.resolve(health);
    await first;
    expect(store.phase).toBe("loading");
    fresh.resolve(health);
    await second;
    expect(store.phase).toBe("success");
  });
});
