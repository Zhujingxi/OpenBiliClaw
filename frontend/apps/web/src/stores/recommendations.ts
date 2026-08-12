import type { RecommendationPage, WebApi } from "../services/api";
import type { CardView } from "@openbiliclaw/presentation";
import { defineStore } from "pinia";
import { ref } from "vue";
import {
  errorMessage,
  isCancellation,
  RequestOwner,
  type LoadPhase,
} from "./state";

export const useRecommendationsStore = defineStore("recommendations", () => {
  const phase = ref<LoadPhase>("idle");
  const page = ref<RecommendationPage>({ items: [] });
  const cards = ref<readonly CardView[]>([]);
  const error = ref<string>();
  const owner = new RequestOwner();

  async function load(api: WebApi): Promise<void> {
    const signal = owner.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      const nextPage = await api.recommendations(signal);
      if (!owner.owns(signal)) return;
      page.value = nextPage;
      cards.value = nextPage.items.map((item) => ({
        version: 1,
        kind: cardKind(item.ref.content_kind.value),
        providerLabel: item.ref.provider_id.value,
        availability: "available",
        data: item.card,
      }));
      phase.value = cards.value.length === 0 ? "empty" : "success";
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  return { phase, page, cards, error, load, cancel: () => owner.cancel() };
});

function cardKind(value: string): CardView["kind"] {
  return ["video", "image", "article", "discussion"].includes(value)
    ? (value as CardView["kind"])
    : "fallback";
}
