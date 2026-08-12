import { defineStore } from "pinia";
import { ref } from "vue";

export const useSessionStore = defineStore("session", () => {
  const deviceId = ref("web-local");
  const authenticated = ref(false);
  const status = ref<"unknown" | "authenticated" | "anonymous">("unknown");

  function establish(nextDeviceId: string, isAuthenticated: boolean): void {
    deviceId.value = nextDeviceId;
    authenticated.value = isAuthenticated;
    status.value = isAuthenticated ? "authenticated" : "anonymous";
  }

  return { deviceId, authenticated, status, establish };
});
