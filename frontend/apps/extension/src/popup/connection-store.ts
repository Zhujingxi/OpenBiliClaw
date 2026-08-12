import { defineStore } from "pinia";
import { ref } from "vue";
import { isLoopbackUrl, type ConnectionState } from "../shared/messages";

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
const STORAGE_KEY = "openbiliclaw.connection";

export const useConnectionStore = defineStore("extension-connection", () => {
  const backendUrl = ref("http://127.0.0.1:8765");
  const deviceToken = ref("");
  const state = ref<ConnectionState>("disconnected");
  const error = ref<string>();

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
      const init: RequestInit = {};
      if (deviceToken.value !== "") {
        init.headers = { authorization: `Bearer ${deviceToken.value}` };
      }
      const response = await fetcher(
        `${backendUrl.value}/v1/runtime/health`,
        init,
      );
      if (!response.ok) {
        state.value = "unavailable";
        error.value = `Backend unavailable (${response.status})`;
        return;
      }
      state.value = "connected";
    } catch {
      state.value = "unavailable";
      error.value = "Backend connection failed";
    }
  }

  return { backendUrl, deviceToken, state, error, configure, check };
});
