import { describe, expect, it, vi } from "vitest";
import { createWebI18n, detectLocale, LOCALE_STORAGE_KEY } from "./i18n";

describe("web locale bootstrap", () => {
  it("detects supported browser locales and falls back to English", () => {
    expect(detectLocale(null, ["zh-HK"])).toBe("zh-TW");
    expect(detectLocale(null, ["zh-CN"])).toBe("zh-CN");
    expect(detectLocale(null, ["fr-FR"])).toBe("en");
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
