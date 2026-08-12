import type { RecommendationPage, WebApi } from "../services/api";
import { defineStore } from "pinia";
import { ref } from "vue";
import { errorMessage, RequestOwner, type LoadPhase } from "./state";

export const useRecommendationsStore = defineStore("recommendations", () => {
  const phase = ref<LoadPhase>("idle");
  const page = ref<RecommendationPage>({ items: [] });
  const error = ref<string>();
  const owner = new RequestOwner();

  async function load(api: WebApi): Promise<void> {
    phase.value = "loading";
    error.value = undefined;
    try {
      page.value = await api.recommendations(owner.next());
      phase.value = page.value.items.length === 0 ? "empty" : "success";
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError")
        return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  return { phase, page, error, load, cancel: () => owner.cancel() };
});
