import { createPinia } from "pinia";
import { ApiError } from "@openbiliclaw/api-client";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { routeParameter } from "../app/routes";
import {
  EMPTY_SOURCE_INVENTORY,
  type SourceListResponse,
  type SourceStatus,
  type WebApi,
} from "../services/api";
import ConnectView from "./ConnectView.vue";
import SearchView from "./SearchView.vue";
import ContentView from "./ContentView.vue";
import ProfileView from "./ProfileView.vue";
import AssistantView from "./AssistantView.vue";
import ProvidersView from "./ProvidersView.vue";
import RecommendationsView from "./RecommendationsView.vue";
import SettingsView from "./SettingsView.vue";
import RuntimeView from "./RuntimeView.vue";
import { createWebI18n, type SupportedLocale } from "../i18n";

function sourceList(
  items: SourceStatus[] = [],
  inventory = EMPTY_SOURCE_INVENTORY,
): SourceListResponse {
  return { items, inventory };
}
const meter = {
  approximate_usage_percent: 25,
  context_window_tokens: 1000,
  estimated_input_tokens: 250,
  excluded_oldest_turns: 0,
};
async function* assistantStream(text = "answer") {
  yield { kind: "turn_started" as const, context_meter: meter };
  yield { kind: "response_delta" as const, delta: text };
  yield {
    kind: "turn_finished" as const,
    context_meter: meter,
    output: { kind: "message" as const, text },
    usage: { input_tokens: 10, output_tokens: 2, request_count: 1 },
  };
}

function api(overrides: Partial<WebApi> = {}): WebApi {
  return {
    login: async () => ({ token: "token", label: "session" }),
    listSources: async () => sourceList(),
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
    assistantTurnStream: () => assistantStream(),
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
        listSources: async () =>
          sourceList([
            { provider_id: "demo", account_id: null, state: "disconnected" },
            { provider_id: "other", account_id: null, state: "disconnected" },
          ]),
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
      ["demo", "Disconnected"],
      ["other", "Disconnected"],
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
      ["other", "Disconnected"],
      ["demo", "Connected"],
    ]);
    await wrapper
      .get('input[type="radio"][value="builtin.manual"]')
      .setValue(true);
    expect(wrapper.get("#field-id").attributes("aria-describedby")).toBe(
      "field-id-help",
    );
  });

  it("surfaces source connection failures without hiding provider status", async () => {
    const wrapper = mountView(
      ConnectView,
      api({
        listSources: async () =>
          sourceList([
            { provider_id: "demo", account_id: null, state: "disconnected" },
          ]),
        connectSource: async () =>
          Promise.reject(
            new ApiError(
              "http",
              "temporary failure",
              500,
              undefined,
              "temporary_failure",
            ),
          ),
      }),
    );
    await vi.waitFor(() => expect(wrapper.find("form").exists()).toBe(true));
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() =>
      expect(wrapper.find('[role="alert"]').exists()).toBe(true),
    );
    expect(wrapper.get('[role="alert"]').text()).toContain("Try again");
    expect(wrapper.get(".source-status strong").text()).toBe("demo");
    expect(wrapper.get(".source-status span").text()).toBe("Disconnected");
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
        listSources: async () =>
          sourceList([
            { provider_id: "demo", account_id: null, state: "disconnected" },
          ]),
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
        listSources: async () =>
          sourceList([
            { provider_id: "demo", account_id: null, state: "connected" },
          ]),
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
      listSources: async () =>
        sourceList([
          {
            provider_id: "weibo",
            account_id: null,
            state: "connected",
            method_id: "builtin.anonymous",
            verification: null,
            capabilities: ["projection", "search"],
          },
        ]),
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
      listSources: async () =>
        sourceList([
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
        ]),
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
      listSources: async () =>
        sourceList([
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
        ]),
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
        assistantTurnStream: () => assistantStream("**answer**\n1. first"),
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
            item.find("strong").text() === "You" &&
            item.find(".message-content").text() === "hello",
        ),
    ).toBe(true);
    expect(wrapper.text()).not.toContain("**answer**");
    expect(
      wrapper.findAll("li").map((item) => item.find("strong").text()),
    ).toEqual(["You", "You", "Assistant"]);
  });

  it("renders accessible live reasoning, context, and sanitized tool states", async () => {
    const lifecycle = async function* () {
      yield {
        kind: "turn_started" as const,
        context_meter: { ...meter, excluded_oldest_turns: 3 },
      };
      yield { kind: "reasoning_started" as const };
      yield { kind: "reasoning_delta" as const, delta: "Textual rationale" };
      yield { kind: "reasoning_finished" as const };
      yield {
        kind: "tool_started" as const,
        name: "Search library",
        arguments: '{"credential":"hidden"}',
      };
      yield {
        kind: "tool_finished" as const,
        name: "Search library",
        status: "failed" as const,
        summary: "No matching items",
        payload: { opaque_id: "hidden" },
      };
      yield { kind: "response_delta" as const, delta: "Safe answer" };
      yield {
        kind: "turn_finished" as const,
        context_meter: { ...meter, excluded_oldest_turns: 3 },
        output: { kind: "message" as const, text: "Safe answer" },
        usage: { input_tokens: 10, output_tokens: 2, request_count: 1 },
      };
    };
    const wrapper = mountView(
      AssistantView,
      api({ assistantTurnStream: lifecycle }),
    );
    await vi.waitFor(() => expect(wrapper.text()).toContain("history"));
    await wrapper.get("textarea").setValue("hello");
    await wrapper
      .get("textarea")
      .trigger("keydown", { shiftKey: true, key: "Enter" });
    expect(wrapper.text()).not.toContain("Safe answer");
    await wrapper.get("textarea").trigger("keydown", { key: "Enter" });
    await vi.waitFor(() => expect(wrapper.text()).toContain("Safe answer"));
    expect(wrapper.get(".context-status").attributes("role")).toBe("status");
    expect(wrapper.text()).toContain("Context ~25%");
    expect(wrapper.text()).toContain("3 older complete turns");
    expect(wrapper.get(".reasoning-card").attributes("open")).toBeUndefined();
    expect(wrapper.get(".reasoning-card").text()).toContain(
      "Textual rationale",
    );
    expect(wrapper.get(".tool-cards").attributes("aria-label")).toBe(
      "Tools used in this turn",
    );
    expect(wrapper.get(".tool-card").text()).toContain(
      "Search libraryNo matching itemsFailed",
    );
    expect(wrapper.text()).not.toContain("credential");
    expect(wrapper.text()).not.toContain("opaque_id");
  });

  it("shows an accessible Stop control while a turn owns the request", async () => {
    const pending = async function* (
      _body: unknown,
      _device: string,
      signal?: AbortSignal,
    ) {
      yield { kind: "turn_started" as const, context_meter: meter };
      await new Promise<never>((_resolve, reject) =>
        signal?.addEventListener("abort", () =>
          reject(new DOMException("Aborted", "AbortError")),
        ),
      );
    };
    const wrapper = mountView(
      AssistantView,
      api({ assistantTurnStream: pending }),
    );
    await vi.waitFor(() => expect(wrapper.text()).toContain("history"));
    await wrapper.get("textarea").setValue("hello");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() =>
      expect(wrapper.get(".stop-button").text()).toContain("Stop"),
    );
    await wrapper.get(".stop-button").trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.find(".stop-button").exists()).toBe(false),
    );
    expect(wrapper.get('button[type="submit"]').text()).toContain("Send");
  });

  it("starts a fresh persisted conversation without deleting prior server history", async () => {
    localStorage.setItem(
      "obc-conversation-id",
      "conv_11111111111111111111111111111111",
    );
    const conversation = vi.fn(api().conversation);
    const wrapper = mountView(AssistantView, api({ conversation }));
    await vi.waitFor(() => expect(wrapper.text()).toContain("history"));
    await wrapper.get(".header-actions button").trigger("click");
    const next = localStorage.getItem("obc-conversation-id");
    expect(next).toMatch(/^conv_[0-9a-f]{32}$/);
    expect(next).not.toBe("conv_11111111111111111111111111111111");
    expect(wrapper.findAll("li")).toHaveLength(0);
    expect(conversation).toHaveBeenCalledTimes(1);
  });

  it.each([
    [
      "en",
      [
        "Action pending: Apply profile update",
        "Recommendations 2 recommendations are available in your feed.",
        "Assistant response unavailable.",
      ],
    ],
    [
      "zh-CN",
      [
        "待处理操作：Apply profile update",
        "推荐 你的信息流中有 2 条推荐。",
        "助手响应不可用。",
      ],
    ],
    [
      "zh-TW",
      [
        "待處理操作：Apply profile update",
        "推薦 你的動態消息中有 2 則推薦。",
        "助理回應無法使用。",
      ],
    ],
  ] as const)(
    "localizes assistant fallbacks in %s",
    async (locale, expected) => {
      const messages = [
        {
          kind: "pending_action",
          action: { effect: "Apply profile update" },
        },
        {
          kind: "recommendations",
          recommendation_ids: ["shown_one", "shown_two"],
        },
        { kind: "future", raw: "hidden" },
      ].map((output, index) => ({
        message_id: `message-${index}`,
        idempotency_key: `key-${index}`,
        role: "assistant" as const,
        content: JSON.stringify(output),
        created_at: "2030-01-01T00:00:00Z",
        references: [],
        tool_calls: [],
        user_correction: false,
      }));
      const wrapper = mount(AssistantView, {
        global: {
          plugins: [createPinia(), webI18n(locale)],
          provide: {
            api: api({
              conversation: async () => ({
                conversation: {
                  conversation_id: "conv",
                  created_at: "2030-01-01T00:00:00Z",
                  updated_at: "2030-01-01T00:00:00Z",
                  retention_days: 30,
                  scope: { local_user_id: "u", device_id: "web-local" },
                },
                messages,
              }),
            }),
          },
        },
      });
      await vi.waitFor(() =>
        expect(wrapper.findAll(".message-content")).toHaveLength(3),
      );
      expect(
        wrapper.findAll(".message-content").map((item) => item.text()),
      ).toEqual(expected);
      expect(wrapper.text()).not.toContain("shown_one");
      expect(wrapper.text()).not.toContain('"kind"');
    },
  );

  it("keeps assistant turns and attaches actionable capability errors", async () => {
    let calls = 0;
    const wrapper = mountView(
      AssistantView,
      api({
        assistantTurnStream: async function* () {
          calls += 1;
          if (calls === 2)
            throw new ApiError(
              "http",
              "capability is not configured",
              503,
              undefined,
              "unavailable_capability",
            );
          yield* assistantStream("first answer");
        },
      }),
    );
    await vi.waitFor(() => expect(wrapper.text()).toContain("history"));
    await wrapper.get("textarea").setValue("first question");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() => expect(wrapper.text()).toContain("first answer"));
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
        listSources: async () =>
          sourceList([
            { provider_id: "demo", account_id: null, state: "disconnected" },
          ]),
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
        listSources: async () =>
          sourceList([
            { provider_id: "ready", account_id: null, state: "connected" },
            { provider_id: "setup", account_id: null, state: "disconnected" },
            { provider_id: "broken", account_id: null, state: "error" },
          ]),
      }),
    );
    await vi.waitFor(() =>
      expect(wrapper.find(".provider-status").exists()).toBe(true),
    );
    expect(wrapper.find('[role="tab"]').exists()).toBe(false);
    expect(
      wrapper.findAll(".provider-status").map((status) => status.text()),
    ).toEqual(["readyConnected", "setupDisconnected", "brokenError"]);
    expect(wrapper.findAll(".status-dot").map((dot) => dot.classes())).toEqual([
      ["status-dot", "status-connected"],
      ["status-dot", "status-disconnected"],
      ["status-dot", "status-error"],
    ]);
    expect(
      wrapper
        .findAll(".verification-line > span:first-child")
        .every((icon) => icon.text() === "○" && !icon.classes("is-verified")),
    ).toBe(true);
  });

  it("shows the candidate pool, feed queue, and per-source inventory", async () => {
    const wrapper = mountView(
      ProvidersView,
      api({
        listSources: async () =>
          sourceList(
            [
              {
                provider_id: "youtube",
                account_id: null,
                state: "connected",
                method_id: "builtin.anonymous",
                capabilities: ["feed", "fetch", "search"],
                verification: {
                  granted_permissions: [],
                  safe_account_identity: "Public access",
                  strength: "live",
                  verified_at: "2030-01-02T03:04:00Z",
                },
              },
            ],
            {
              pool_count: 12,
              queue_count: 5,
              archived_count: 3,
              by_provider: [
                { key: "youtube", pool_count: 9, queue_count: 4 },
                { key: "bilibili", pool_count: 3, queue_count: 1 },
              ],
              by_content_kind: [
                { key: "video", pool_count: 10, queue_count: 4 },
                { key: "article", pool_count: 2, queue_count: 1 },
              ],
            },
          ),
      }),
    );
    await vi.waitFor(() =>
      expect(wrapper.findAll(".source-metrics dd")[0]?.text()).toBe("1"),
    );
    expect(
      wrapper.findAll(".source-metrics dd").map((item) => item.text()),
    ).toEqual(["1", "5", "4", "12"]);
    expect(wrapper.get(".kind-list").text()).toContain(
      "Video10 pool · 4 queue",
    );
    expect(wrapper.get(".queue-provider-list").text()).toContain("youtube4");
    expect(
      wrapper.findAll(".provider-facts dd").map((item) => item.text()),
    ).toEqual(["9", "4", "Live"]);
    expect(wrapper.get(".verification-line").text()).toContain("Verified");
    expect(
      wrapper.get(".verification-line > span:first-child").classes(),
    ).toContain("is-verified");
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
    expect(wrapper.findAll("details.settings-section")).toHaveLength(1);
    expect(wrapper.get("#model-endpoint-help").text()).toContain(
      "full API base URL",
    );
    expect(wrapper.get("#model-endpoint").attributes("placeholder")).toBe(
      "Use catalog default",
    );
    expect(wrapper.get(".field-wide .field-hint").text()).toContain(
      "never returned",
    );
    expect(wrapper.get("#model-name").attributes("aria-describedby")).toBe(
      "model-name-help",
    );
    expect(wrapper.get("#model-endpoint").attributes("aria-describedby")).toBe(
      "model-endpoint-help",
    );
    expect(wrapper.get("#model-api-key").attributes("aria-describedby")).toBe(
      "model-api-key-help",
    );
    expect(wrapper.get(".provider-gallery").attributes("role")).toBe("group");
    await wrapper.get("#language").setValue("zh-CN");
    expect(wrapper.get("h1").text()).toBe("设置");
    await wrapper.get("#language").setValue("en");
    expect(
      wrapper.get("details.settings-section").attributes("open"),
    ).not.toBeUndefined();
    expect(wrapper.get(".status-badge").classes()).toContain("status-warning");
    expect(wrapper.findAll(".provider-option")).toHaveLength(1);
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
    expect(wrapper.get("#model-endpoint").attributes("placeholder")).toBe(
      "https://api.example.com/v1",
    );
    expect(wrapper.find("fieldset.capabilities").exists()).toBe(true);
    expect(wrapper.findAll(".capability-options label")).toHaveLength(5);
  });

  it("gives ambiguous search input nearby guidance", async () => {
    const wrapper = mountView(
      SearchView,
      api({
        listSources: async () =>
          sourceList([
            { provider_id: "demo", account_id: null, state: "connected" },
          ]),
      }),
    );
    await vi.waitFor(() =>
      expect(wrapper.find("#search-query").exists()).toBe(true),
    );
    expect(wrapper.get("#search-query").attributes("aria-describedby")).toBe(
      "search-query-help",
    );
    expect(wrapper.get("#search-query-help").text()).toContain("topic");
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
    await vi.waitFor(() =>
      expect(error.text()).toContain("request could not be completed"),
    );
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

function webI18n(locale: SupportedLocale) {
  return createWebI18n(
    { getItem: () => locale, setItem: () => undefined },
    [locale],
    { lang: locale },
  );
}
