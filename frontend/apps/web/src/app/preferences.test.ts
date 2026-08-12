import { createPinia } from "pinia";
import { describe, expect, it, vi } from "vitest";
import { usePreferencesStore } from "../stores/preferences";
import { initializePreferences } from "./preferences";

describe("preference bootstrap", () => {
  it("hydrates before installing persistence and survives reload", () => {
    const values = new Map([
      ["obc-density", "compact"],
      ["obc-reduced-motion", "true"],
    ]);
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: vi.fn((key: string, value: string) => values.set(key, value)),
      removeItem: vi.fn(),
      clear: vi.fn(),
      key: vi.fn(() => null),
      get length() {
        return values.size;
      },
    } satisfies Storage;
    const firstPinia = createPinia();
    initializePreferences(storage, firstPinia);
    expect(usePreferencesStore(firstPinia).density).toBe("compact");
    expect(storage.setItem).toHaveBeenCalledWith("obc-density", "compact");
    const secondPinia = createPinia();
    initializePreferences(storage, secondPinia);
    expect(usePreferencesStore(secondPinia).reducedMotion).toBe(true);
  });
});
