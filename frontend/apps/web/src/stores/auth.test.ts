import { ApiError } from "@openbiliclaw/api-client";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AUTH_TOKEN_KEY, authenticatedFetch, useAuthStore } from "./auth";
import type { WebApi } from "../services/api";

const api = (overrides: Partial<WebApi>): WebApi =>
  ({
    login: vi.fn(),
    recommendations: vi.fn(),
    ...overrides,
  }) as WebApi;

describe("authentication", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    location.hash = "#/login";
  });

  it("stores a successful login token and redirects", async () => {
    const auth = useAuthStore();
    await auth.login(
      api({
        login: vi
          .fn()
          .mockResolvedValue({ token: "session-token", label: "session" }),
      }),
      "secret",
    );

    expect(localStorage.getItem(AUTH_TOKEN_KEY)).toBe("session-token");
    expect(auth.status).toBe("authenticated");
    expect(location.hash).toBe("#/recommendations");
  });

  it("clears a stored token and redirects when an API response is 401", async () => {
    localStorage.setItem(AUTH_TOKEN_KEY, "expired");
    const unauthorized = vi.fn();
    const request = vi
      .fn()
      .mockResolvedValue(new Response("{}", { status: 401 }));
    const fetcher = authenticatedFetch(
      request as typeof fetch,
      localStorage,
      unauthorized,
    );

    const response = await fetcher("https://local.test/v1/recommendations");

    const init = request.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBe(
      "Bearer expired",
    );
    expect(response.status).toBe(401);
    expect(localStorage.getItem(AUTH_TOKEN_KEY)).toBeNull();
    expect(unauthorized).toHaveBeenCalledOnce();
  });

  it("bypasses login when authentication is not configured", async () => {
    const auth = useAuthStore();
    await auth.initialize(
      api({ recommendations: vi.fn().mockResolvedValue({ items: [] }) }),
    );
    expect(auth.status).toBe("not-configured");

    auth.requireLogin();
    await auth.login(
      api({
        login: vi
          .fn()
          .mockRejectedValue(new ApiError("http", "not configured", 503)),
      }),
      "anything",
    );
    expect(auth.status).toBe("not-configured");
    expect(location.hash).toBe("#/recommendations");
  });

  it("keeps a valid receiver when the wrapped fetch requires one", async () => {
    // Chrome 151+ throws "Illegal invocation" for a detached native fetch.
    const receiverAware = vi.fn(function (this: unknown): Promise<Response> {
      if (this === undefined) {
        throw new TypeError("Illegal invocation");
      }
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
    const fetcher = authenticatedFetch(
      receiverAware as unknown as typeof fetch,
      localStorage,
      vi.fn(),
    );

    const response = await fetcher("https://local.test/v1/recommendations");

    expect(response.status).toBe(200);
    expect(receiverAware).toHaveBeenCalledOnce();
  });

  it("requires login when the startup probe is 401", async () => {
    const auth = useAuthStore();
    await auth.initialize(
      api({
        recommendations: vi
          .fn()
          .mockRejectedValue(new ApiError("http", "unauthorized", 401)),
      }),
    );

    expect(auth.status).toBe("required");
    expect(location.hash).toBe("#/login");
  });

  it("fails closed when the startup probe hits a network or server error", async () => {
    localStorage.setItem(AUTH_TOKEN_KEY, "maybe-stale");
    const auth = useAuthStore();
    await auth.initialize(
      api({
        recommendations: vi
          .fn()
          .mockRejectedValue(new ApiError("network", "offline")),
      }),
    );

    expect(auth.status).toBe("required");
    expect(localStorage.getItem(AUTH_TOKEN_KEY)).toBeNull();
    expect(location.hash).toBe("#/login");
  });
});
