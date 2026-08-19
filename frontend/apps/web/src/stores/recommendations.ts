import type { RecommendationPage, WebApi } from "../services/api";
import { uuid } from "@openbiliclaw/api-client";
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
  const feedbackState = ref<Record<string, "liked" | "dismissed">>({});
  const feedbackError = ref<Record<string, string>>({});
  const owner = new RequestOwner();
  const feedbackControllers = new Map<string, AbortController>();
  const exposed = new Set<string>();

  function applyPage(nextPage: RecommendationPage): void {
    page.value = nextPage;
    feedbackState.value = {};
    feedbackError.value = {};
    exposed.clear();
    cards.value = nextPage.items.map((item) => ({
      shownId: item.shown_id,
      version: 1,
      kind: cardKind(item.ref.content_kind.value),
      providerLabel: item.ref.provider_id.value,
      availability: "available",
      data: item.card,
    }));
    phase.value = cards.value.length === 0 ? "empty" : "success";
  }

  async function load(api: WebApi): Promise<void> {
    const signal = owner.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      const nextPage = await api.recommendations(signal);
      if (owner.owns(signal)) applyPage(nextPage);
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  async function refresh(api: WebApi): Promise<void> {
    const signal = owner.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      await api.refreshRecommendations(
        { idempotency_key: uuid(), maximum_items: 50 },
        signal,
      );
      if (!owner.owns(signal)) return;
      const nextPage = await api.recommendations(signal);
      if (owner.owns(signal)) applyPage(nextPage);
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  async function like(api: WebApi, card: CardView): Promise<void> {
    await submitFeedback(api, card, "liked");
  }

  function markExposed(card: CardView): void {
    if (card.shownId !== undefined) exposed.add(card.shownId);
  }

  async function dismiss(api: WebApi, card: CardView): Promise<void> {
    await submitFeedback(api, card, "dismissed");
  }

  async function submitFeedback(
    api: WebApi,
    card: CardView,
    kind: "liked" | "dismissed",
  ): Promise<void> {
    if (card.shownId === undefined) return;
    const shownId = card.shownId;
    delete feedbackError.value[shownId];
    feedbackControllers.get(shownId)?.abort();
    const controller = new AbortController();
    feedbackControllers.set(shownId, controller);
    try {
      await api.feedback(
        {
          idempotency_key: `${shownId}:${kind}`,
          shown_id: shownId,
          content_ref: card.data.ref,
          kind,
          exposed: kind === "dismissed" && exposed.has(shownId),
        },
        controller.signal,
      );
      if (feedbackControllers.get(shownId) === controller)
        feedbackState.value[shownId] = kind;
    } catch (caught) {
      if (isCancellation(caught)) return;
      if (feedbackControllers.get(shownId) === controller) {
        feedbackError.value[shownId] =
          errorStatus(caught) === 404 || errorStatus(caught) === 409
            ? "This recommendation expired. Refresh the feed and try again."
            : errorMessage(caught);
      }
    } finally {
      if (feedbackControllers.get(shownId) === controller)
        feedbackControllers.delete(shownId);
    }
  }

  return {
    phase,
    page,
    cards,
    error,
    feedbackState,
    feedbackError,
    load,
    refresh,
    like,
    dismiss,
    markExposed,
    cancel: () => {
      owner.cancel();
      for (const controller of feedbackControllers.values()) controller.abort();
      feedbackControllers.clear();
    },
  };
});

function errorStatus(value: unknown): number | undefined {
  if (typeof value !== "object" || value === null || !("status" in value))
    return undefined;
  return typeof value.status === "number" ? value.status : undefined;
}

function cardKind(value: string): CardView["kind"] {
  return ["video", "image", "article", "discussion"].includes(value)
    ? (value as CardView["kind"])
    : "fallback";
}
