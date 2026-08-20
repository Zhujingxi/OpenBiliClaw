import { config } from "@vue/test-utils";
import { createExtensionI18n } from "./i18n";

config.global.plugins = [
  createExtensionI18n(
    { getItem: () => "en", setItem: () => undefined },
    ["en"],
    { lang: "en" },
  ),
];
