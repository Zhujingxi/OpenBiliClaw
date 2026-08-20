import { createPinia } from "pinia";
import { createApp } from "vue";
import PopupApp from "./PopupApp.vue";
import { createExtensionI18n } from "../i18n";

createApp(PopupApp)
  .use(createPinia())
  .use(
    createExtensionI18n(
      localStorage,
      navigator.languages,
      document.documentElement,
    ),
  )
  .mount("#app");
