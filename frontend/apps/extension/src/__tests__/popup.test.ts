import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import PopupApp from "../popup/PopupApp.vue";

it("renders an accessible connection shell before recipes are loaded", () => {
  const wrapper = mount(PopupApp, { global: { plugins: [createPinia()] } });
  expect(wrapper.get("main").attributes("aria-labelledby")).toBe(
    "extension-title",
  );
  expect(wrapper.get('input[name="backendUrl"]').attributes("aria-label")).toBe(
    "Backend URL",
  );
  expect(
    wrapper.get('input[name="deviceToken"]').attributes("aria-label"),
  ).toBe("Extension token");
  expect(wrapper.text()).not.toMatch(/cookie|task dispatch|browser session/i);
});
