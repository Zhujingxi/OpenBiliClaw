import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import PopupApp from "../popup/PopupApp.vue";

it("renders an accessible guided connection shell and switches locale live", async () => {
  const wrapper = mount(PopupApp, { global: { plugins: [createPinia()] } });
  expect(wrapper.get("main").attributes("aria-labelledby")).toBe(
    "extension-title",
  );
  expect(wrapper.get('input[name="backendUrl"]').attributes("aria-label")).toBe(
    "Backend base URL",
  );
  expect(
    wrapper.get('input[name="deviceToken"]').attributes("aria-label"),
  ).toBe("Extension token");
  expect(wrapper.get("#backend-help").text()).toContain("include /v1");
  expect(wrapper.get("#token-help").text()).toContain("never shown again");
  expect(wrapper.text()).not.toMatch(/cookie|task dispatch|browser session/i);
  await wrapper.get('select[name="locale"]').setValue("zh-CN");
  expect(wrapper.get("#connection-title").text()).toBe("后端连接");
});

it("does not repopulate a saved write-only token", () => {
  localStorage.setItem(
    "openbiliclaw.connection",
    JSON.stringify({
      backendUrl: "http://127.0.0.1:8420",
      deviceToken: "saved-secret",
    }),
  );
  const wrapper = mount(PopupApp, { global: { plugins: [createPinia()] } });
  const token = wrapper.get<HTMLInputElement>('input[name="deviceToken"]');
  expect(token.element.value).toBe("");
  expect(token.attributes("required")).toBeUndefined();
  localStorage.clear();
});
