import type { ContentResponse, SearchResponse, WebApi } from "../services/api";
import { defineStore } from "pinia";
import { ref } from "vue";
import {
  errorMessage,
  isCancellation,
  RequestOwner,
  type LoadPhase,
} from "./state";

export const useContentStore = defineStore("content", () => {
  const phase = ref<LoadPhase>("idle");
  const results = ref<SearchResponse>({ items: [] });
  const detail = ref<ContentResponse>();
  const error = ref<string>();
  const owner = new RequestOwner();

  async function search(
    api: WebApi,
    providerId: string,
    query: string,
  ): Promise<void> {
    const signal = owner.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      const next = await api.search(providerId, query, signal);
      if (!owner.owns(signal)) return;
      results.value = next;
      phase.value = next.items.length === 0 ? "empty" : "success";
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }
  async function fetchDetail(api: WebApi, reference: string): Promise<void> {
    const signal = owner.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      const next = await api.content(reference, signal);
      if (!owner.owns(signal)) return;
      detail.value = next;
      phase.value = "success";
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }
  return {
    phase,
    results,
    detail,
    error,
    search,
    fetchDetail,
    cancel: () => owner.cancel(),
  };
});
