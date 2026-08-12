import { createPinia } from "pinia";
import { createApp } from "vue";
import PopupApp from "./PopupApp.vue";

createApp(PopupApp).use(createPinia()).mount("#app");
