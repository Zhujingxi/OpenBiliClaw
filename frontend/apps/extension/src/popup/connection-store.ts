import { ApiClient } from "@openbiliclaw/api-client";
import { defineStore } from "pinia";
import { ref } from "vue";
import { isLoopbackUrl, type ConnectionState } from "../shared/connection";

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
const STORAGE_KEY = "openbiliclaw.connection";
const DEFAULT_BACKEND = "http://127.0.0.1:8420";

type RuntimeResponse =
  import("@openbiliclaw/api-client/generated").components["schemas"]["RuntimeResponse"];
function isRuntimeResponse(value: unknown): value is RuntimeResponse {
  if (
    typeof value !== "object" ||
    value === null ||
    !Object.hasOwn(value, "health")
  )
    return false;
  const record = value as Record<string, unknown>;
  const health: unknown = record.health;
  if (typeof health !== "object" || health === null) return false;
  const fields = health as Record<string, unknown>;
  return (
    typeof fields.checked_at === "string" &&
    typeof fields.component_id === "string" &&
    Array.isArray(fields.jobs) &&
    typeof fields.status === "string"
  );
}

export const useConnectionStore = defineStore("extension-connection", () => {
  const backendUrl = ref(DEFAULT_BACKEND);
  const deviceToken = ref("");
  const state = ref<ConnectionState>("disconnected");
  const error = ref<string>();

  function hydrate(storage: StorageLike = localStorage): void {
    const saved = storage.getItem(STORAGE_KEY);
    if (saved === null) return;
    try {
      const value: unknown = JSON.parse(saved);
      if (typeof value !== "object" || value === null) return;
      const fields = value as Record<string, unknown>;
      if (
        isLoopbackUrl(fields.backendUrl) &&
        typeof fields.deviceToken === "string" &&
        fields.deviceToken.length <= 512
      ) {
        backendUrl.value = fields.backendUrl.replace(/\/$/, "");
        deviceToken.value = fields.deviceToken;
      }
    } catch {
      storage.removeItem(STORAGE_KEY);
    }
  }

  function configure(
    url: string,
    token: string,
    storage: StorageLike = localStorage,
  ): void {
    if (!isLoopbackUrl(url))
      throw new Error("Backend must be a loopback HTTP(S) URL");
    if (token.length < 1 || token.length > 512)
      throw new Error("Invalid device token");
    backendUrl.value = url.replace(/\/$/, "");
    deviceToken.value = token;
    storage.setItem(
      STORAGE_KEY,
      JSON.stringify({ backendUrl: backendUrl.value, deviceToken: token }),
    );
  }

  async function check(fetcher: typeof fetch = fetch): Promise<void> {
    state.value = "checking";
    error.value = undefined;
    try {
      const headers =
        deviceToken.value === ""
          ? undefined
          : { authorization: `Bearer ${deviceToken.value}` };
      const client = new ApiClient(backendUrl.value, fetcher);
      await client.request({
        path: "/v1/runtime/health",
        method: "get",
        validate: isRuntimeResponse,
        ...(headers === undefined ? {} : { headers }),
      });
      state.value = "connected";
    } catch (caught: unknown) {
      state.value = "unavailable";
      const status =
        caught instanceof Error && "status" in caught
          ? caught.status
          : undefined;
      error.value =
        typeof status === "number"
          ? `Backend unavailable (${status})`
          : "Backend connection failed";
    }
  }

  return { backendUrl, deviceToken, state, error, hydrate, configure, check };
});
