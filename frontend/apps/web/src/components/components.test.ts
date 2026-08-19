import { readFileSync } from "node:fs";
import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import AppNavigation from "./AppNavigation.vue";
import AsyncState from "./AsyncState.vue";
import App from "../App.vue";
import type { WebApi } from "../services/api";

const api: WebApi = {
  login: async () => ({ token: "token", label: "session" }),
  listSources: async () => [],
  connectSource: async () => {
    throw new Error("unused");
  },
  recommendations: async () => ({ items: [] }),
  refreshRecommendations: async () => ({ decision: "run" }),
  feedback: async () => {
    throw new Error("unused");
  },
  profile: async () => ({
    profile: { version: 1, preference_summary: [], insights: [] },
  }),
  editProfile: async () => {
    throw new Error("unused");
  },
  assistantTurn: async () => {
    throw new Error("unused");
  },
  conversation: async () => {
    throw new Error("unused");
  },
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
  currentModel: async () => {
    throw new Error("unused");
  },
  updateModel: async () => {
    throw new Error("unused");
  },
  events: async function* () {
    yield* [] as never[];
  },
};

describe("web accessibility", () => {
  it("marks current desktop and mobile navigation accessibly", () => {
    const desktop = mount(AppNavigation, { props: { current: "profile" } });
    expect(desktop.get("nav").attributes("aria-label")).toBe(
      "Primary navigation",
    );
    expect(desktop.get('[aria-current="page"] span:last-child').text()).toBe(
      "Taste profile",
    );
    const mobile = mount(AppNavigation, {
      props: { current: "settings", mobile: true },
    });
    expect(mobile.get("nav").attributes("aria-label")).toBe(
      "Mobile navigation",
    );
    expect(mobile.get('[aria-current="page"] span:last-child').text()).toBe(
      "Settings",
    );
    const source = readFileSync("src/styles.css", "utf8");
    expect(source).toContain('.mobile-nav a[aria-current="page"]');
    expect(source).toContain("background: var(--brand-soft);");
  });

  it.each([
    ["loading", "status"],
    ["empty", "status"],
    ["error", "alert"],
  ] as const)("announces %s state", (phase, role) => {
    expect(
      mount(AsyncState, { props: { phase, error: "failed" } })
        .find(`[role="${role}"]`)
        .exists(),
    ).toBe(true);
  });

  it("uses a wrapping mobile grid and shrinkable controls at narrow widths", () => {
    const source = readFileSync("src/styles.css", "utf8");
    expect(source).toContain(
      "grid-template-columns: repeat(5, minmax(0, 1fr))",
    );
    expect(source).toContain(
      'input:not([type="checkbox"]):not([type="radio"])',
    );
    expect(source).toContain("min-width: 0;");
    expect(source).toContain("overflow-wrap: anywhere;");
  });

  it("has skip navigation, labels, distinct responsive layouts, and Alt+Left keyboard path", async () => {
    location.hash = "#/profile";
    const back = vi.spyOn(history, "back").mockImplementation(() => undefined);
    const wrapper = mount(App, {
      attachTo: document.body,
      global: { plugins: [createPinia()], provide: { api } },
    });
    const skip = wrapper.get(".skip-link");
    expect(skip.attributes("href")).toBe("#main");
    expect(wrapper.find(".responsive-layout").exists()).toBe(true);
    expect(wrapper.findAll("main")).toHaveLength(1);
    expect(wrapper.findAllComponents({ name: "ProfileView" })).toHaveLength(1);
    expect(document.title).toBe("Profile · OpenBiliClaw");
    await skip.trigger("click");
    expect(location.hash).toBe("#/profile");
    expect(document.activeElement?.id).toBe("main");
    await wrapper
      .get(".shell")
      .trigger("keydown", { altKey: true, key: "ArrowLeft" });
    expect(back).toHaveBeenCalled();
    wrapper.unmount();
    back.mockRestore();
  });

  it("announces unknown routes while retaining the recommendations fallback", () => {
    location.hash = "#/missing";
    const wrapper = mount(App, {
      global: { plugins: [createPinia()], provide: { api } },
    });
    expect(wrapper.get(".route-notice").attributes("role")).toBe("status");
    expect(wrapper.text()).toContain("Page not found; showing For you");
    expect(document.title).toBe("For you · OpenBiliClaw");
    wrapper.unmount();
  });
});
