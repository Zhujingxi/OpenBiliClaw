import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "@openbiliclaw/api-client";
import type { WebApi } from "../services/api";
import LoginView from "./LoginView.vue";

beforeEach(() => localStorage.clear());

it("submits the password and displays localized authorization errors", async () => {
  const login = vi
    .fn()
    .mockRejectedValueOnce(
      new ApiError("http", "Invalid password", 401, undefined, "unauthorized"),
    )
    .mockResolvedValueOnce({ token: "token", label: "session" });
  const wrapper = mount(LoginView, {
    global: {
      plugins: [createPinia()],
      provide: { api: { login } as unknown as WebApi },
    },
  });

  await wrapper.get('input[type="password"]').setValue("wrong");
  await wrapper.get("form").trigger("submit");
  await vi.waitFor(() =>
    expect(wrapper.get('[role="alert"]').text()).toContain(
      "Authorization is required",
    ),
  );

  await wrapper.get('input[type="password"]').setValue("right");
  await wrapper.get("form").trigger("submit");
  await vi.waitFor(() => expect(login).toHaveBeenLastCalledWith("right"));
  expect(localStorage.getItem("openbiliclaw.auth-token")).toBe("token");
});
