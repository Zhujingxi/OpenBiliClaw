import { ApiClient, deviceIdentity } from "@openbiliclaw/api-client";
import { createPinia } from "pinia";
import { createApp } from "vue";
import App from "./App.vue";
import { initializePreferences } from "./app/preferences";
import { createWebApi } from "./services/api";
import { authenticatedFetch, useAuthStore } from "./stores/auth";
import { useSessionStore } from "./stores/session";

async function bootstrap(): Promise<void> {
  const pinia = createPinia();
  initializePreferences(localStorage, pinia);
  const deviceId = deviceIdentity(localStorage);
  useSessionStore(pinia).establish(deviceId, false);
  const auth = useAuthStore(pinia);
  const fetcher = authenticatedFetch(fetch, localStorage, () =>
    auth.requireLogin(),
  );
  const api = createWebApi(new ApiClient(location.origin, fetcher, deviceId));
  await auth.initialize(api);
  if (auth.status === "required") location.hash = "#/login";
  else if (location.hash === "#/login") location.hash = "#/recommendations";

  const app = createApp(App);
  app.use(pinia);
  app.provide("api", api);
  app.mount("#app");
}

void bootstrap();
