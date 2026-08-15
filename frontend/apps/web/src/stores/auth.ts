import { ApiError } from "@openbiliclaw/api-client";
import { defineStore } from "pinia";
import { ref } from "vue";
import type { WebApi } from "../services/api";
import { errorMessage } from "./state";

export const AUTH_TOKEN_KEY = "openbiliclaw.auth-token";
export type AuthStatus =
  | "checking"
  | "required"
  | "authenticated"
  | "not-configured";

interface AuthStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** Attach the durable session token and invalidate it on any unauthorized response. */
export function authenticatedFetch(
  fetcher: typeof fetch,
  storage: AuthStorage = localStorage,
  onUnauthorized: () => void = () => {
    location.hash = "#/login";
  },
): typeof fetch {
  // Bind once: a captured native fetch invoked without its Window receiver throws
  // "Illegal invocation" on Chrome 151+ (see api-client client.test.ts precedent).
  const bound = fetcher.bind(globalThis);
  return async (input, init) => {
    const headers = new Headers(init?.headers);
    const token = storage.getItem(AUTH_TOKEN_KEY)?.trim();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await bound(input, { ...init, headers });
    if (response.status === 401) {
      storage.removeItem(AUTH_TOKEN_KEY);
      onUnauthorized();
    }
    return response;
  };
}

export const useAuthStore = defineStore("auth", () => {
  const status = ref<AuthStatus>("checking");
  const error = ref<string>();
  const loading = ref(false);

  function requireLogin(): void {
    status.value = "required";
    localStorage.removeItem(AUTH_TOKEN_KEY);
    if (location.hash !== "#/login") location.hash = "#/login";
  }

  async function initialize(api: WebApi): Promise<void> {
    const hasToken = Boolean(localStorage.getItem(AUTH_TOKEN_KEY)?.trim());
    try {
      await api.recommendations();
      status.value = hasToken ? "authenticated" : "not-configured";
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        requireLogin();
        return;
      }
      // Fail closed: a network/server failure must not be mistaken for disabled auth.
      localStorage.removeItem(AUTH_TOKEN_KEY);
      status.value = "required";
      if (location.hash !== "#/login") location.hash = "#/login";
    }
  }

  async function login(api: WebApi, password: string): Promise<void> {
    loading.value = true;
    error.value = undefined;
    try {
      const response = await api.login(password);
      localStorage.setItem(AUTH_TOKEN_KEY, response.token);
      status.value = "authenticated";
      location.hash = "#/recommendations";
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 503) {
        localStorage.removeItem(AUTH_TOKEN_KEY);
        status.value = "not-configured";
        location.hash = "#/recommendations";
        return;
      }
      error.value = errorMessage(caught);
    } finally {
      loading.value = false;
    }
  }

  return { status, error, loading, initialize, login, requireLogin };
});
