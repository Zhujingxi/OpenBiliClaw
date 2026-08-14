import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { routeParameter } from "../app/routes";
import type { WebApi } from "../services/api";
import ConnectView from "./ConnectView.vue";
import SearchView from "./SearchView.vue";
import ContentView from "./ContentView.vue";
import AssistantView from "./AssistantView.vue";
import ProvidersView from "./ProvidersView.vue";
import RecommendationsView from "./RecommendationsView.vue";
import SettingsView from "./SettingsView.vue";

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
function mountView(component: Parameters<typeof mount>[0], web = api()) {
  return mount(component, {
    global: { plugins: [createPinia()], provide: { api: web } },
  });
}
beforeEach(() => {
  location.hash = "";
  localStorage.clear();
});

describe("web view behavior", () => {
  it("connects a source using the typed mutation", async () => {
    const connectSource = vi.fn(api().connectSource);
    const wrapper = mountView(ConnectView, api({ connectSource }));
    expect(wrapper.text()).not.toContain("Source connected.");
    await wrapper.get("#provider-id").setValue("demo");
    await wrapper.get("form").trigger("submit");
    expect(connectSource).toHaveBeenCalledWith(
      expect.objectContaining({ provider_id: "demo" }),
      expect.any(AbortSignal),
    );
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain("Source connected."),
    );
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
      api({ search: async () => ({ items: [preview] }) }),
    );
    await wrapper.get("#search-query").setValue("result");
    await wrapper.get("form").trigger("submit");
    await wrapper.get("ul button").trigger("click");
    expect(routeParameter(location.hash)).toBe(JSON.stringify(preview.ref));
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
    expect(wrapper.get("[aria-live='polite']").classes()).toContain(
      "message-content",
    );
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
    await vi.waitFor(() => expect(wrapper.text()).toContain("connected"));
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
    const wrapper = mountView(
      RecommendationsView,
      api({ feedback, recommendations }),
    );
    await vi.waitFor(() =>
      expect(wrapper.find("article.obc-card").exists()).toBe(true),
    );
    await wrapper.get("button").trigger("click");
    await vi.waitFor(() => expect(recommendations).toHaveBeenCalledTimes(2));
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
