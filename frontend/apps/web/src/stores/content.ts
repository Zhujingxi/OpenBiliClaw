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
  const searchPhase = ref<LoadPhase>("idle");
  const detailPhase = ref<LoadPhase>("idle");
  const results = ref<SearchResponse>({ items: [] });
  const detail = ref<ContentResponse>();
  const searchError = ref<string>();
  const detailError = ref<string>();
  const searchOwner = new RequestOwner();
  const detailOwner = new RequestOwner();
  // Form state lives in the store so navigating to a detail and back does not
  // wipe the user's last search while the results themselves persist.
  const lastProvider = ref("bilibili");
  const lastQuery = ref("");

  async function search(
    api: WebApi,
    providerId: string,
    query: string,
  ): Promise<void> {
    const signal = searchOwner.next();
    searchPhase.value = "loading";
    searchError.value = undefined;
    lastProvider.value = providerId;
    lastQuery.value = query;
    try {
      const next = await api.search(providerId, query, signal);
      if (!searchOwner.owns(signal)) return;
      results.value = next;
      searchPhase.value = next.items.length === 0 ? "empty" : "success";
    } catch (caught) {
      if (isCancellation(caught) || !searchOwner.owns(signal)) return;
      searchError.value = errorMessage(caught);
      searchPhase.value = "error";
    }
  }

  async function fetchDetail(api: WebApi, reference: string): Promise<void> {
    const signal = detailOwner.next();
    detailPhase.value = "loading";
    detailError.value = undefined;
    try {
      const next = await api.content(reference, signal);
      if (!detailOwner.owns(signal)) return;
      detail.value = next;
      detailPhase.value = "success";
    } catch (caught) {
      if (isCancellation(caught) || !detailOwner.owns(signal)) return;
      detailError.value = errorMessage(caught);
      detailPhase.value = "error";
    }
  }

  return {
    searchPhase,
    detailPhase,
    results,
    detail,
    searchError,
    detailError,
    lastProvider,
    lastQuery,
    search,
    fetchDetail,
    cancelSearch: () => searchOwner.cancel(),
    cancelDetail: () => detailOwner.cancel(),
    cancel: () => {
      searchOwner.cancel();
      detailOwner.cancel();
    },
  };
});
