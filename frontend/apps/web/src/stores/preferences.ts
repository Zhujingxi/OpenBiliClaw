import { defineStore } from "pinia";
import { ref, watch } from "vue";

export type Density = "comfortable" | "compact";

export const usePreferencesStore = defineStore("preferences", () => {
  const density = ref<Density>("comfortable");
  const reducedMotion = ref(false);

  function hydrate(storage: Pick<Storage, "getItem">): void {
    if (storage.getItem("obc-density") === "compact") density.value = "compact";
    reducedMotion.value = storage.getItem("obc-reduced-motion") === "true";
  }

  function persist(storage: Pick<Storage, "setItem">): () => void {
    return watch(
      [density, reducedMotion],
      () => {
        storage.setItem("obc-density", density.value);
        storage.setItem("obc-reduced-motion", String(reducedMotion.value));
      },
      { immediate: true },
    );
  }

  return { density, reducedMotion, hydrate, persist };
});
