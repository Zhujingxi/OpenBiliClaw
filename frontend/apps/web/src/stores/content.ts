import type { ContentResponse, SearchResponse, WebApi } from "../services/api";
import { defineStore } from "pinia";
import { ref } from "vue";
import { errorMessage, RequestOwner, type LoadPhase } from "./state";

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
    phase.value = "loading";
    try {
      results.value = await api.search(providerId, query, owner.next());
      phase.value = results.value.items.length === 0 ? "empty" : "success";
    } catch (caught) {
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }
  async function fetchDetail(api: WebApi, reference: string): Promise<void> {
    phase.value = "loading";
    try {
      detail.value = await api.content(reference, owner.next());
      phase.value = "success";
    } catch (caught) {
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
