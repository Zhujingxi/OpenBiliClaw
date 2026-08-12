import type { SourceStatus, WebApi } from "../services/api";
import { defineStore } from "pinia";
import { ref } from "vue";
import { errorMessage, RequestOwner, type LoadPhase } from "./state";

export const useSourcesStore = defineStore("sources", () => {
  const phase = ref<LoadPhase>("idle");
  const items = ref<readonly SourceStatus[]>([]);
  const error = ref<string>();
  const owner = new RequestOwner();

  async function load(api: WebApi): Promise<void> {
    phase.value = "loading";
    error.value = undefined;
    try {
      items.value = await api.listSources(owner.next());
      phase.value = items.value.length === 0 ? "empty" : "success";
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError")
        return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  return { phase, items, error, load, cancel: () => owner.cancel() };
});
