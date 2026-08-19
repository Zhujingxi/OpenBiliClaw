import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { routeParameter } from "../app/routes";
import type { WebApi } from "../services/api";
import ConnectView from "./ConnectView.vue";
import SearchView from "./SearchView.vue";
import ContentView from "./ContentView.vue";
import ProfileView from "./ProfileView.vue";
import AssistantView from "./AssistantView.vue";
import ProvidersView from "./ProvidersView.vue";
import RecommendationsView from "./RecommendationsView.vue";
import SettingsView from "./SettingsView.vue";
import RuntimeView from "./RuntimeView.vue";

function api(overrides: Partial<WebApi> = {}): WebApi {
  return {
    login: async () => ({ token: "token", label: "session" }),
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
    refreshRecommendations: async () => ({ decision: "run" }),
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
    editProfile: async () => {
      throw new Error("unused");
    },
    assistantTurn: async () => ({
      output: { kind: "message", text: "answer" },
    }),
    conversation: async () => ({
      conversation: {
        conversation_id: "conv",
        created_at: "2030-01-01T00:00:00Z",
        updated_at: "2030-01-01T00:00:00Z",
        retention_days: 30,
        scope: { local_user_id: "u", device_id: "web-local" },
      },
      messages: [
        {
          message_id: "m1",
          idempotency_key: "k",
          role: "user",
          content: "history",
          created_at: "2030-01-01T00:00:00Z",
          references: [],
          tool_calls: [],
          user_correction: false,
        },
      ],
    }),
    runtimeHealth: async () => {
      throw new Error("unused");
    },
    search: async () => ({ items: [] }),
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
    content: async () => ({
      content: {
        ref: {
          provider_id: { value: "demo" },
          content_kind: { value: "video" },
          provider_content_id: "one",
          canonical_url: "https://example.test/one",
        },
        schema_version: 1,
        payload: {},
      },
    }),
    events: async function* () {
      yield* [] as never[];
    },
    ...overrides,
  };
}
function mountView(
  component: Parameters<typeof mount>[0],
  web = api(),
  pinia = createPinia(),
) {
  return mount(component, {
    global: { plugins: [pinia], provide: { api: web } },
  });
}
beforeEach(() => {
  location.hash = "";
  localStorage.clear();
});

describe("web view behavior", () => {
  it("connects a source using the typed mutation", async () => {
    const connectSource = vi.fn(api().connectSource);
    const wrapper = mountView(
      ConnectView,
      api({
        connectSource,
        listSources: async () => [
          { provider_id: "demo", account_id: null, state: "disconnected" },
          { provider_id: "other", account_id: null, state: "disconnected" },
        ],
      }),
    );
    expect(wrapper.text()).not.toContain("Source connected.");
    await vi.waitFor(() =>
      expect(wrapper.find("#provider-id").exists()).toBe(true),
    );
    expect(
      wrapper
        .findAll(".source-status")
        .map((item) => [item.get("strong").text(), item.get("span").text()]),
    ).toEqual([
      ["demo", "disconnected"],
      ["other", "disconnected"],
    ]);
    await wrapper.get("#provider-id").setValue("demo");
    await wrapper.get("form").trigger("submit");
    expect(connectSource).toHaveBeenCalledWith(
      expect.objectContaining({ provider_id: "demo" }),
      expect.any(AbortSignal),
    );
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain("Source connected."),
    );
    expect(
      wrapper
        .findAll(".source-status")
        .map((item) => [item.get("strong").text(), item.get("span").text()]),
    ).toEqual([
      ["other", "disconnected"],
      ["demo", "connected"],
    ]);
  });

  it("surfaces source connection failures without hiding provider status", async () => {
    const wrapper = mountView(
      ConnectView,
      api({
        listSources: async () => [
          { provider_id: "demo", account_id: null, state: "disconnected" },
        ],
        connectSource: async () =>
          Promise.reject(new Error("temporary failure")),
      }),
    );
    await vi.waitFor(() => expect(wrapper.find("form").exists()).toBe(true));
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() =>
      expect(wrapper.find('[role="alert"]').exists()).toBe(true),
    );
    expect(wrapper.get('[role="alert"]').text()).toContain("Try again");
    expect(wrapper.get(".source-status strong").text()).toBe("demo");
    expect(wrapper.get(".source-status span").text()).toBe("disconnected");
  });

  it("turns empty product states into actionable guidance", async () => {
    const recommendations = mountView(RecommendationsView);
    await vi.waitFor(() =>
      expect(recommendations.find('a[href="#/connect"]').exists()).toBe(true),
    );
    expect(recommendations.text()).toContain(
      "No recommendations are available yet",
    );

    const providers = mountView(
      ProvidersView,
      api({
        listSources: async () => [
          { provider_id: "demo", account_id: null, state: "disconnected" },
        ],
      }),
    );
    await vi.waitFor(() =>
      expect(providers.find('a[href="#/connect"]').exists()).toBe(true),
    );
    const noProviders = mountView(ProvidersView);
    await vi.waitFor(() =>
      expect(noProviders.find('a[href="#/connect"]').exists()).toBe(true),
    );

    const profile = mountView(ProfileView);
    expect(profile.text()).toContain("read-only profile");
    expect(profile.get('a[href="#/connect"]').text()).toBe("Connect a source");

    const content = mountView(ContentView);
    expect(content.get('a[href="#/search"]').text()).toBe("Search");
  });

  it("navigates from search results to encoded content detail", async () => {
    const preview = {
      title: "Result",
      summary: "Summary",
      source_timestamp: "2030-01-01T00:00:00Z",
      ref: {
        provider_id: { value: "demo" },
        content_kind: { value: "video" },
        provider_content_id: "one",
        canonical_url: "https://example.test/one",
      },
      provenance: {
        native_schema_version: 1,
        projected_at: "2030-01-01T00:00:00Z",
        ref: {
          provider_id: { value: "demo" },
          content_kind: { value: "video" },
          provider_content_id: "one",
          canonical_url: "https://example.test/one",
        },
      },
    };
    const wrapper = mountView(
      SearchView,
      api({
        listSources: async () => [
          { provider_id: "demo", account_id: null, state: "connected" },
        ],
        search: async () => ({ items: [preview] }),
      }),
    );
    await vi.waitFor(() =>
      expect(wrapper.find("#search-query").exists()).toBe(true),
    );
    await wrapper.get("#search-query").setValue("result");
    await wrapper.get("form").trigger("submit");
    await wrapper.get("ul button").trigger("click");
    expect(routeParameter(location.hash)).toBe(JSON.stringify(preview.ref));
  });

  it("opens results from fetch-less providers on their canonical URL", async () => {
    const web = api({
      listSources: async () => [
        {
          provider_id: "weibo",
          account_id: null,
          state: "connected",
          method_id: "builtin.anonymous",
          verification: null,
          capabilities: ["projection", "search"],
        },
      ],
      search: async () => ({
        items: [
          {
            ref: {
              provider_id: { value: "weibo" },
              content_kind: { value: "post" },
              provider_content_id: "5012345678901234",
              canonical_url: "https://weibo.com/status/P0stBid",
            },
            title: "A weibo post",
            summary: "body",
            source_timestamp: "2025-01-02T00:00:00Z",
            provenance: {
              ref: {
                provider_id: { value: "weibo" },
                content_kind: { value: "post" },
                provider_content_id: "5012345678901234",
                canonical_url: "https://weibo.com/status/P0stBid",
              },
              native_schema_version: 1,
              projected_at: "2025-01-02T00:00:00Z",
            },
          },
        ],
      }),
    });
    const opened: string[] = [];
    vi.stubGlobal(
      "open",
      vi.fn((url: string) => {
        opened.push(url);
      }),
    );
    const wrapper = mountView(SearchView, web);
    await vi.waitFor(() =>
      expect(wrapper.get("#search-provider").text()).toContain("weibo"),
    );
    await wrapper.get("#search-query").setValue("anything");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() => expect(wrapper.text()).toContain("A weibo post"));
    await wrapper.get("ul button").trigger("click");
    expect(opened).toEqual(["https://weibo.com/status/P0stBid"]);
    expect(location.hash).not.toContain("#/content/");
    vi.unstubAllGlobals();
  });

  it("excludes providers that declare no search capability from the select", async () => {
    const web = api({
      listSources: async () => [
        {
          provider_id: "v2ex",
          account_id: null,
          state: "connected",
          method_id: "builtin.anonymous",
          verification: null,
          capabilities: ["creator", "feed", "fetch", "projection"],
        },
        {
          provider_id: "bangumi",
          account_id: null,
          state: "connected",
          method_id: "builtin.anonymous",
          verification: null,
          capabilities: ["feed", "fetch", "projection", "search"],
        },
      ],
    });
    const wrapper = mountView(SearchView, web);
    await vi.waitFor(() =>
      expect(wrapper.get("#search-provider").text()).toContain("bangumi"),
    );
    expect(wrapper.get("#search-provider").text()).not.toContain("v2ex");
  });

  it("offers connected providers as a select and keeps form state across remounts", async () => {
    const search = vi.fn(api().search);
    const web = api({
      search,
      listSources: async () => [
        {
          provider_id: "youtube",
          account_id: null,
          state: "connected",
          method_id: "builtin.anonymous",
          verification: null,
        },
        {
          provider_id: "bilibili",
          account_id: null,
          state: "disconnected",
          method_id: null,
          verification: null,
        },
      ],
    });
    const pinia = createPinia();
    const wrapper = mountView(SearchView, web, pinia);
    await vi.waitFor(() =>
      expect(wrapper.get("#search-provider").text()).toContain("youtube"),
    );
    await wrapper.get("#search-provider").setValue("youtube");
    await wrapper.get("#search-query").setValue("kept query");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() =>
      expect(search).toHaveBeenCalledWith(
        "youtube",
        "kept query",
        expect.any(AbortSignal),
      ),
    );
    wrapper.unmount();
    const remounted = mountView(SearchView, web, pinia);
    expect(
      (remounted.get("#search-provider").element as HTMLSelectElement).value,
    ).toBe("youtube");
    expect(
      (remounted.get("#search-query").element as HTMLInputElement).value,
    ).toBe("kept query");
  });

  it("renders available detail metadata and the canonical link", async () => {
    const ref = {
      provider_id: { value: "youtube" },
      content_kind: { value: "video" },
      provider_content_id: "ix9cRaBkVe0",
      canonical_url: "https://www.youtube.com/watch?v=ix9cRaBkVe0",
    };
    location.hash = `#/content/${encodeURIComponent(JSON.stringify(ref))}`;
    const wrapper = mountView(
      ContentView,
      api({
        content: async () => ({
          content: {
            ref,
            schema_version: 1,
            payload: {
              title: "A Real Title",
              channel: { id: "UC1", name: "A Channel" },
              description: "line one\nline two",
              thumbnail_url: "https://img.test/thumb.jpg",
            },
          },
        }),
      }),
    );
    await vi.waitFor(() =>
      expect(wrapper.get("h2").text()).toBe("A Real Title"),
    );
    expect(wrapper.text()).toContain("A Channel");
    expect(wrapper.text()).toContain("line one");
    expect(wrapper.get("img").attributes("src")).toBe(
      "/v1/media?url=https%3A%2F%2Fimg.test%2Fthumb.jpg",
    );
    const link = wrapper.get("a");
    expect(link.attributes("href")).toBe(ref.canonical_url);
    expect(link.attributes("rel")).toContain("noopener");
  });

  it("renders bangumi-shaped payloads via image_url and summary fallbacks", async () => {
    const ref = {
      provider_id: { value: "bangumi" },
      content_kind: { value: "subject" },
      provider_content_id: "241088",
      canonical_url: "https://bgm.tv/subject/241088",
    };
    location.hash = `#/content/${encodeURIComponent(JSON.stringify(ref))}`;
    const wrapper = mountView(
      ContentView,
      api({
        content: async () => ({
          content: {
            ref,
            schema_version: 1,
            payload: {
              title: "孤独摇滚！",
              summary: "乐队……那是阴暗角色也能闪耀起来的唯一地方。",
              image_url: "https://lain.bgm.tv/pic/cover/l/42.jpg",
              subject_type: "anime",
            },
          },
        }),
      }),
    );
    await vi.waitFor(() => expect(wrapper.get("h2").text()).toBe("孤独摇滚！"));
    expect(wrapper.text()).toContain("乐队");
    expect(wrapper.get("img").attributes("src")).toBe(
      "/v1/media?url=https%3A%2F%2Flain.bgm.tv%2Fpic%2Fcover%2Fl%2F42.jpg",
    );
    expect(wrapper.get("a").attributes("href")).toBe(ref.canonical_url);
  });

  it("loads and refetches JSON content references on same-route hash changes", async () => {
    const first = JSON.stringify({
      provider_id: { value: "demo" },
      content_kind: { value: "video" },
      provider_content_id: "one",
      canonical_url: "https://example.test/one",
    });
    const second = JSON.stringify({
      provider_id: { value: "demo" },
      content_kind: { value: "video" },
      provider_content_id: "two",
      canonical_url: "https://example.test/two",
    });
    location.hash = `#/content/${encodeURIComponent(first)}`;
    const content = vi.fn(api().content);
    mountView(ContentView, api({ content }));
    await vi.waitFor(() =>
      expect(content).toHaveBeenCalledWith(first, expect.any(AbortSignal)),
    );
    location.hash = `#/content/${encodeURIComponent(second)}`;
    dispatchEvent(new HashChangeEvent("hashchange"));
    await vi.waitFor(() =>
      expect(content).toHaveBeenLastCalledWith(second, expect.any(AbortSignal)),
    );
    expect(content).toHaveBeenCalledTimes(2);
  });

  it("renders assistant history, submitted user text, and safe plain output", async () => {
    const wrapper = mountView(
      AssistantView,
      api({
        assistantTurn: async () => ({
          output: { kind: "message", text: "**answer**\n1. first" },
        }),
      }),
    );
    await vi.waitFor(() => expect(wrapper.text()).toContain("history"));
    await wrapper.get("textarea").setValue("hello");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() => expect(wrapper.text()).toContain("answer"));
    expect(
      wrapper
        .findAll("li")
        .some(
          (item) =>
            item.find("strong").text() === "user" &&
            item.find(".message-content").text() === "hello",
        ),
    ).toBe(true);
    expect(wrapper.text()).not.toContain("**answer**");
    expect(
      wrapper.findAll("li").map((item) => item.find("strong").text()),
    ).toEqual(["user", "user", "assistant"]);
  });

  it("keeps assistant turns and attaches actionable capability errors", async () => {
    let calls = 0;
    const wrapper = mountView(
      AssistantView,
      api({
        assistantTurn: async () => {
          calls += 1;
          if (calls === 2) throw new Error("capability is not configured");
          return { output: { kind: "message", text: "first answer" } };
        },
      }),
    );
    await vi.waitFor(() => expect(wrapper.text()).toContain("history"));
    await wrapper.get("textarea").setValue("first question");
    await wrapper.get("form").trigger("submit");
    await wrapper.get("textarea").setValue("second question");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() =>
      expect(wrapper.find('[role="alert"]').exists()).toBe(true),
    );
    expect(wrapper.text()).toContain("first question");
    expect(wrapper.text()).toContain("first answer");
    expect(wrapper.text()).toContain("second question");
    expect(wrapper.get('[role="alert"] a').attributes("href")).toBe(
      "#/settings",
    );
    expect(
      wrapper
        .get("ol")
        .element.compareDocumentPosition(wrapper.get("form").element),
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("replaces dead search forms with an actionable connected-source explanation", async () => {
    const wrapper = mountView(
      SearchView,
      api({
        listSources: async () => [
          { provider_id: "demo", account_id: null, state: "disconnected" },
        ],
      }),
    );
    await vi.waitFor(() =>
      expect(wrapper.find('a[href="#/connect"]').exists()).toBe(true),
    );
    expect(wrapper.find("form").exists()).toBe(false);
  });

  it("renders provider statuses as a list without invalid tab roles", async () => {
    const wrapper = mountView(
      ProvidersView,
      api({
        listSources: async () => [
          { provider_id: "demo", account_id: null, state: "connected" },
        ],
      }),
    );
    await vi.waitFor(() =>
      expect(wrapper.find(".provider-status").exists()).toBe(true),
    );
    expect(wrapper.find('[role="tab"]').exists()).toBe(false);
    const status = wrapper.get(".provider-status");
    expect(status.findAll(":scope > *").map((item) => item.text())).toEqual([
      "demo",
      "connected",
    ]);
  });

  it("loads catalog model settings and saves a write-only key", async () => {
    const updateModel = vi.fn(async () => ({
      current: {
        model: {
          provider: "deepseek",
          model_name: "deepseek-chat",
          endpoint: null,
          secret_configured: true,
          protocol: "openai" as const,
          capabilities: {
            tools: true,
            structured_output: false,
            vision: false,
            context_tokens: 128000,
            streaming: true,
            reasoning: false,
          },
        },
        embedding: {
          provider: "openai",
          model_name: "",
          endpoint: null,
          secret_configured: false,
        },
      },
      reloaded: false,
      restart_required: true,
    }));
    const wrapper = mountView(
      SettingsView,
      api({
        modelCatalog: async () => ({
          providers: [
            {
              id: "deepseek",
              name: "DeepSeek",
              env: ["DEEPSEEK_API_KEY"],
              protocol: "openai",
              models: [
                {
                  id: "deepseek-chat",
                  name: "DeepSeek Chat",
                  reasoning: false,
                  tool_call: true,
                  structured_output: false,
                  context_limit: 128000,
                },
              ],
            },
          ],
        }),
        currentModel: async () => ({
          current: {
            model: {
              provider: "deepseek",
              model_name: "deepseek-chat",
              endpoint: null,
              secret_configured: false,
              protocol: "openai",
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
        updateModel,
      }),
    );
    await vi.waitFor(() =>
      expect(wrapper.find("#model-provider").exists()).toBe(true),
    );
    await wrapper.get("#model-api-key").setValue("write-only-value");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() => expect(updateModel).toHaveBeenCalledOnce());
    expect(updateModel).toHaveBeenCalledWith(
      {
        provider: "deepseek",
        model_name: "deepseek-chat",
        api_key: "write-only-value",
      },
      expect.any(AbortSignal),
    );
    await vi.waitFor(() =>
      expect(
        wrapper.get<HTMLInputElement>("#model-api-key").element.value,
      ).toBe(""),
    );
    expect(wrapper.text()).toContain("Restart OpenBiliClaw");
    await wrapper.get('input[type="checkbox"]').setValue(true);
    expect(wrapper.find("fieldset.capabilities").exists()).toBe(true);
    expect(wrapper.findAll(".capability-options label")).toHaveLength(5);
  });

  it("keeps the selected catalog pair while filtering and displays server configuration", async () => {
    const wrapper = mountView(
      SettingsView,
      api({
        modelCatalog: async () => ({
          providers: [
            {
              id: "openai",
              name: "OpenAI",
              env: ["OPENAI_API_KEY"],
              protocol: "openai",
              models: [
                {
                  id: "gpt-4o",
                  name: "GPT-4o",
                  reasoning: false,
                  tool_call: true,
                  structured_output: true,
                  context_limit: 128000,
                },
              ],
            },
            {
              id: "anthropic",
              name: "Anthropic",
              env: ["ANTHROPIC_API_KEY"],
              protocol: "anthropic",
              models: [],
            },
          ],
        }),
        currentModel: async () => ({
          current: {
            model: {
              provider: "openai",
              model_name: "gpt-4o",
              endpoint: null,
              secret_configured: true,
              protocol: "openai",
              capabilities: null,
            },
            embedding: {
              provider: "openai",
              model_name: "text-embedding-3-small",
              endpoint: "https://api.example.test",
              secret_configured: true,
            },
          },
          reloaded: true,
          restart_required: false,
        }),
      }),
    );
    await vi.waitFor(() =>
      expect(wrapper.find("#model-provider").exists()).toBe(true),
    );
    await wrapper.get("#model-search").setValue("anthropic");
    expect(
      wrapper.get<HTMLSelectElement>("#model-provider").element.value,
    ).toBe("openai");
    expect(wrapper.get<HTMLSelectElement>("#model-name").element.value).toBe(
      "gpt-4o",
    );
    expect(wrapper.get(".current-configuration").text()).toContain(
      "text-embedding-3-small",
    );
    expect(wrapper.get(".current-configuration").text()).toContain(
      "Reloaded in this processYes",
    );
  });

  it("renders runtime timestamp, supervised jobs, status treatment, and events detail", async () => {
    const wrapper = mountView(
      RuntimeView,
      api({
        runtimeHealth: async () => ({
          health: {
            component_id: "runtime.supervisor",
            status: "healthy",
            checked_at: "2030-01-01T00:00:00Z",
            jobs: [
              {
                job_id: "recommendation.replenishment",
                last_result: "success",
                runs_started: 7,
                runs_completed: 7,
                active_runs: 0,
              },
            ],
          },
        }),
        events: async function* () {
          yield {
            kind: "job",
            event_id: 1,
            component_id: "runtime.supervisor",
            status: "success",
          };
          await new Promise(() => undefined);
        },
      }),
    );
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain("recommendation.replenishment"),
    );
    expect(wrapper.get("time").attributes("datetime")).toBe(
      "2030-01-01T00:00:00Z",
    );
    expect(wrapper.get(".health-badge").classes()).toContain("health-healthy");
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain("Recent runtime events (1)"),
    );
    wrapper.unmount();
  });

  it("renders model catalog loading, empty, and error states", async () => {
    const pending = new Promise<never>(() => undefined);
    const loading = mountView(
      SettingsView,
      api({ modelCatalog: () => pending, currentModel: () => pending }),
    );
    await vi.waitFor(() =>
      expect(loading.text()).toContain("Loading model catalog"),
    );
    const empty = mountView(SettingsView);
    await vi.waitFor(() =>
      expect(empty.text()).toContain("No catalog providers"),
    );
    const error = mountView(
      SettingsView,
      api({ modelCatalog: async () => Promise.reject(new Error("offline")) }),
    );
    await vi.waitFor(() => expect(error.text()).toContain("offline"));
  });

  it("resolves recommendation content, refreshes, and wires shared card feedback", async () => {
    const feedback = vi.fn(api().feedback);
    const recommendations = vi.fn(async () => ({
      items: [
        {
          shown_id: "shown_11111111111111111111111111111111",
          ref: {
            provider_id: { value: "demo" },
            content_kind: { value: "video" },
            provider_content_id: "one",
            canonical_url: "https://example.test/one",
          },
          card: {
            ref: {
              provider_id: { value: "demo" },
              content_kind: { value: "video" },
              provider_content_id: "one",
              canonical_url: "https://example.test/one",
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
                provider_content_id: "one",
                canonical_url: "https://example.test/one",
              },
              native_schema_version: 1,
              projected_at: "2030-01-01T00:00:00Z",
            },
          },
          reason: "Recommended for relevance and freshness.",
          selection: {
            candidate_id: "one",
            recommendation_id: "r1",
            rank: 1,
            score: 1,
            seed: 1,
            selected_at: "2030-01-01T00:00:00Z",
            contributions: [],
          },
        },
      ],
    }));
    const refreshRecommendations = vi.fn(async () => ({ decision: "run" }));
    const wrapper = mountView(
      RecommendationsView,
      api({ feedback, recommendations, refreshRecommendations }),
    );
    await vi.waitFor(() =>
      expect(wrapper.find("article.obc-card").exists()).toBe(true),
    );
    await wrapper.get("button.secondary-action").trigger("click");
    await vi.waitFor(() =>
      expect(refreshRecommendations).toHaveBeenCalledOnce(),
    );
    expect(refreshRecommendations).toHaveBeenCalledWith(
      expect.objectContaining({ maximum_items: 50 }),
      expect.any(AbortSignal),
    );
    await vi.waitFor(() => expect(recommendations).toHaveBeenCalledTimes(2));
    expect(wrapper.get("article a").attributes("href")).toContain("#/content/");
    await wrapper.get('[aria-label="Like recommendation"]').trigger("click");
    await vi.waitFor(() => expect(feedback).toHaveBeenCalledOnce());
    expect(feedback).toHaveBeenCalledWith(
      expect.objectContaining({
        shown_id: "shown_11111111111111111111111111111111",
        kind: "liked",
      }),
      expect.any(AbortSignal),
    );
  });
});
