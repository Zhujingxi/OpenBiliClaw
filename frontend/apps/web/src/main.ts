import { ApiClient, deviceIdentity } from "@openbiliclaw/api-client";
import { createPinia } from "pinia";
import { createApp } from "vue";
import App from "./App.vue";
import { createWebApi } from "./services/api";
import { initializePreferences } from "./app/preferences";
import { useSessionStore } from "./stores/session";

const pinia = createPinia();
initializePreferences(localStorage, pinia);
const deviceId = deviceIdentity(localStorage);
useSessionStore(pinia).establish(deviceId, false);
const app = createApp(App);
app.use(pinia);
app.provide(
  "api",
  createWebApi(new ApiClient(location.origin, fetch, deviceId)),
);
app.mount("#app");
