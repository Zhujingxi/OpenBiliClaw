import { config } from "@vue/test-utils";
import { createWebI18n } from "./i18n";

config.global.plugins = [
  createWebI18n({ getItem: () => "en", setItem: () => undefined }, ["en"], {
    lang: "en",
  }),
];
