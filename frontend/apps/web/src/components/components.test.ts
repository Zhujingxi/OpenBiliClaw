import { readFileSync } from "node:fs";
import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@openbiliclaw/api-client";
import AppNavigation from "./AppNavigation.vue";
import AsyncState from "./AsyncState.vue";
import LocalizedError from "./LocalizedError.vue";
import App from "../App.vue";
import { EMPTY_SOURCE_INVENTORY, type WebApi } from "../services/api";
import { createWebI18n, type SupportedLocale } from "../i18n";
import { errorMessage } from "../stores/state";

const api: WebApi = {
  login: async () => ({ token: "token", label: "session" }),
  listSources: async () => ({
    items: [],
    inventory: EMPTY_SOURCE_INVENTORY,
  }),
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
  assistantTurnStream: async function* () {
    yield* [] as never[];
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
    expect(source).toContain("color: var(--muted-foreground);");
    expect(source).toContain("outline: 3px solid var(--ring);");
  });

  it.each([
    ["loading", "status"],
    ["empty", "status"],
    ["error", "alert"],
  ] as const)("announces %s state", (phase, role) => {
    expect(
      mount(AsyncState, {
        props: { phase, error: { key: "errors.requestFailed" } },
      })
        .find(`[role="${role}"]`)
        .exists(),
    ).toBe(true);
  });

  it.each([
    ["en", "This capability is not configured."],
    ["zh-CN", "尚未配置此功能。"],
    ["zh-TW", "尚未設定此功能。"],
  ] as const)("renders stable API failures in %s", (locale, expected) => {
    const error = errorMessage(
      new ApiError(
        "http",
        "capability is not configured: secret payload",
        503,
        undefined,
        "unavailable_capability",
      ),
    );
    const wrapper = mount(LocalizedError, {
      props: { error },
      global: { plugins: [webI18n(locale)] },
    });
    expect(wrapper.text()).toBe(expected);
    expect(wrapper.text()).not.toContain("secret payload");
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
    const assistant = readFileSync("src/views/AssistantView.vue", "utf8");
    expect(assistant).toContain("@media (max-width: 48rem)");
    expect(assistant).toContain("grid-template-columns: minmax(0, 1fr) auto");
    expect(assistant).toContain("min-width: 2.8rem");
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

  it("keeps language-selector focus and scroll while route changes still focus the heading", async () => {
    location.hash = "#/settings";
    const wrapper = mount(App, {
      attachTo: document.body,
      global: { plugins: [createPinia()], provide: { api } },
    });
    const language = wrapper.get<HTMLSelectElement>("#language");
    language.element.focus();
    document.documentElement.scrollTop = 123;
    await language.setValue("zh-CN");
    expect(document.activeElement).toBe(language.element);
    expect(document.documentElement.scrollTop).toBe(123);
    expect(document.title).toBe("设置 · OpenBiliClaw");
    await language.setValue("en");

    location.hash = "#/profile";
    dispatchEvent(new HashChangeEvent("hashchange"));
    await vi.waitFor(() =>
      expect(document.activeElement).toBe(wrapper.get("main h1").element),
    );
    expect(document.documentElement.scrollTop).toBe(0);
    wrapper.unmount();
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

function webI18n(locale: SupportedLocale) {
  return createWebI18n(
    { getItem: () => locale, setItem: () => undefined },
    [locale],
    { lang: locale },
  );
}
