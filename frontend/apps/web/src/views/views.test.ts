import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WebApi } from "../services/api";
import ConnectView from "./ConnectView.vue";
import SearchView from "./SearchView.vue";
import ContentView from "./ContentView.vue";
import AssistantView from "./AssistantView.vue";
import ProvidersView from "./ProvidersView.vue";
import RecommendationsView from "./RecommendationsView.vue";

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
});

describe("web view behavior", () => {
  it("connects a source using the typed mutation", async () => {
    const connectSource = vi.fn(api().connectSource);
    const wrapper = mountView(ConnectView, api({ connectSource }));
    await wrapper.get("#provider-id").setValue("demo");
    await wrapper.get("form").trigger("submit");
    expect(connectSource).toHaveBeenCalledWith(
      expect.objectContaining({ provider_id: "demo" }),
      expect.any(AbortSignal),
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
    expect(location.hash).toContain("#/content/https%3A%2F%2Fexample.test");
  });

  it("loads the route-selected content reference", async () => {
    location.hash = "#/content/demo%2Fvideo%2Fone";
    const content = vi.fn(api().content);
    mountView(ContentView, api({ content }));
    await vi.waitFor(() =>
      expect(content).toHaveBeenCalledWith(
        "demo/video/one",
        expect.any(AbortSignal),
      ),
    );
  });

  it("renders assistant history and complete output", async () => {
    const wrapper = mountView(AssistantView);
    await vi.waitFor(() => expect(wrapper.text()).toContain("history"));
    await wrapper.get("textarea").setValue("hello");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() => expect(wrapper.text()).toContain("answer"));
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
  });

  it("resolves recommendation content into the shared CardRenderer", async () => {
    const wrapper = mountView(
      RecommendationsView,
      api({
        recommendations: async () => ({
          items: [
            {
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
        }),
      }),
    );
    await vi.waitFor(() =>
      expect(wrapper.find("article.obc-card").exists()).toBe(true),
    );
  });
});
