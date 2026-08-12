import { ApiClient } from "@openbiliclaw/api-client";
import { createPinia } from "pinia";
import { createApp } from "vue";
import App from "./App.vue";
import { createWebApi } from "./services/api";

const app = createApp(App);
app.use(createPinia());
app.provide("api", createWebApi(new ApiClient(location.origin)));
app.mount("#app");
