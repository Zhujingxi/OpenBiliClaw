import { describe, expect, it, vi } from "vitest";
import { createExtensionI18n, detectLocale, LOCALE_STORAGE_KEY } from "../i18n";
import en from "../locales/en";
import zhCN from "../locales/zh-CN";
import zhTW from "../locales/zh-TW";

describe("extension locale bootstrap", () => {
  it("detects Simplified and Traditional Chinese and falls back to English", () => {
    expect(detectLocale(null, ["zh-SG"])).toBe("zh-CN");
    expect(detectLocale(null, ["zh-Hant-HK"])).toBe("zh-TW");
    expect(detectLocale(null, ["de"])).toBe("en");
  });

  it.each([
    ["en", "Backend connection"],
    ["zh-CN", "后端连接"],
    ["zh-TW", "後端連線"],
  ] as const)("renders extension output for %s", (selected, expected) => {
    const i18n = createExtensionI18n(
      { getItem: () => selected, setItem: () => undefined },
      ["en"],
      { lang: "" },
    );
    expect(i18n.global.t("connection")).toBe(expected);
  });

  it("keeps every extension catalog key in parity", () => {
    expect(catalogKeys(zhCN)).toEqual(catalogKeys(en));
    expect(catalogKeys(zhTW)).toEqual(catalogKeys(en));
  });

  it("restores and live-persists the selected locale", () => {
    const storage = { getItem: vi.fn(() => "zh-CN"), setItem: vi.fn() };
    const root = { lang: "" };
    const i18n = createExtensionI18n(storage, ["en"], root);
    expect(root.lang).toBe("zh-CN");
    i18n.global.locale.value = "zh-TW";
    expect(root.lang).toBe("zh-TW");
    expect(storage.setItem).toHaveBeenLastCalledWith(
      LOCALE_STORAGE_KEY,
      "zh-TW",
    );
  });
});

function catalogKeys(value: object, prefix = ""): string[] {
  return Object.entries(value)
    .flatMap(([key, item]) => {
      const path = prefix ? `${prefix}.${key}` : key;
      return typeof item === "object" && item !== null
        ? catalogKeys(item, path)
        : [path];
    })
    .sort();
}
