import { watch } from "vue";
import { createI18n } from "vue-i18n";
import en from "./locales/en";
import zhCN from "./locales/zh-CN";
import zhTW from "./locales/zh-TW";

export const supportedLocales = ["en", "zh-CN", "zh-TW"] as const;
export type SupportedLocale = (typeof supportedLocales)[number];
export const LOCALE_STORAGE_KEY = "obc-extension-locale";
interface Catalog {
  [key: string]: string | Catalog;
}
const messages: Record<SupportedLocale, Catalog> = {
  en,
  "zh-CN": zhCN,
  "zh-TW": zhTW,
};

export function matchLocale(
  value: string | null | undefined,
): SupportedLocale | undefined {
  if (!value) return undefined;
  const normalized = value.toLowerCase();
  if (normalized === "en" || normalized.startsWith("en-")) return "en";
  if (
    ["zh-tw", "zh-hk", "zh-mo", "zh-hant"].some(
      (tag) => normalized === tag || normalized.startsWith(`${tag}-`),
    )
  )
    return "zh-TW";
  if (normalized === "zh" || normalized.startsWith("zh-")) return "zh-CN";
  return undefined;
}

export function detectLocale(
  saved: string | null,
  browserLanguages: readonly string[],
): SupportedLocale {
  return (
    matchLocale(saved) ??
    browserLanguages.map(matchLocale).find((locale) => locale !== undefined) ??
    "en"
  );
}

export function createExtensionI18n(
  storage: Pick<Storage, "getItem" | "setItem">,
  browserLanguages: readonly string[],
  root: Pick<HTMLElement, "lang">,
) {
  const locale = detectLocale(
    storage.getItem(LOCALE_STORAGE_KEY),
    browserLanguages,
  );
  const i18n = createI18n({
    legacy: false,
    locale,
    fallbackLocale: "en",
    messages,
  });
  watch(
    i18n.global.locale,
    (next) => {
      storage.setItem(LOCALE_STORAGE_KEY, next);
      root.lang = next;
    },
    { immediate: true, flush: "sync" },
  );
  return i18n;
}
