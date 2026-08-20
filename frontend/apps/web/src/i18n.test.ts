import { describe, expect, it, vi } from "vitest";
import { createWebI18n, detectLocale, LOCALE_STORAGE_KEY } from "./i18n";
import en from "./locales/en";
import zhCN from "./locales/zh-CN";
import zhTW from "./locales/zh-TW";

function keys(value: object, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return typeof child === "object" ? keys(child as object, path) : [path];
  });
}

describe("web locale bootstrap", () => {
  it("detects supported browser locales and falls back to English", () => {
    expect(detectLocale(null, ["zh-HK"])).toBe("zh-TW");
    expect(detectLocale(null, ["zh-CN"])).toBe("zh-CN");
    expect(detectLocale(null, ["fr-FR"])).toBe("en");
  });

  it("keeps every supported catalog complete", () => {
    expect(keys(zhCN).sort()).toEqual(keys(en).sort());
    expect(keys(zhTW).sort()).toEqual(keys(en).sort());
  });

  it.each([
    ["en", "Settings", "Language"],
    ["zh-CN", "设置", "语言"],
    ["zh-TW", "設定", "語言"],
  ] as const)(
    "renders core navigation and settings output for %s",
    (selected, settings, language) => {
      const i18n = createWebI18n(
        { getItem: () => selected, setItem: () => undefined },
        ["en"],
        { lang: "" },
      );
      expect(i18n.global.t("nav.settings")).toBe(settings);
      expect(i18n.global.t("settings.language")).toBe(language);
    },
  );

  it.each(["en", "zh-CN", "zh-TW"] as const)(
    "uses full versioned model endpoint guidance in %s",
    (selected) => {
      const i18n = createWebI18n(
        { getItem: () => selected, setItem: () => undefined },
        [selected],
        { lang: "" },
      );
      expect(i18n.global.t("settings.endpointHelp")).toContain(
        "https://api.example.com/v1",
      );
      expect(i18n.global.t("settings.endpointPlaceholder")).toBe(
        "https://api.example.com/v1",
      );
    },
  );

  it("uses singular English Assistant feedback for count one", () => {
    const i18n = createWebI18n(
      { getItem: () => "en", setItem: () => undefined },
      ["en"],
      { lang: "" },
    );
    expect(i18n.global.t("assistant.contextExcluded", 1)).toContain(
      "1 older complete turn is",
    );
    expect(i18n.global.t("assistant.recommendationsAvailable", 1)).toBe(
      "1 recommendation is available in your feed.",
    );
  });

  it("prefers, persists, and applies a saved locale", () => {
    const storage = { getItem: vi.fn(() => "zh-TW"), setItem: vi.fn() };
    const root = { lang: "" };
    const i18n = createWebI18n(storage, ["en-US"], root);
    expect(i18n.global.locale.value).toBe("zh-TW");
    expect(root.lang).toBe("zh-TW");
    expect(storage.setItem).toHaveBeenCalledWith(LOCALE_STORAGE_KEY, "zh-TW");
    i18n.global.locale.value = "en";
    expect(root.lang).toBe("en");
  });
});
