import type { Pinia } from "pinia";
import { usePreferencesStore } from "../stores/preferences";

export function initializePreferences(storage: Storage, pinia: Pinia): void {
  const preferences = usePreferencesStore(pinia);
  preferences.hydrate(storage);
  preferences.persist(storage);
}
