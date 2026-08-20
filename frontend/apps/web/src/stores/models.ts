import { defineStore } from "pinia";
import { computed, ref } from "vue";
import type {
  ModelCatalogResponse,
  ModelConfigurationRequest,
  ModelConfigurationResponse,
  WebApi,
} from "../services/api";
import {
  errorMessage,
  isCancellation,
  RequestOwner,
  type LoadPhase,
  type UiError,
} from "./state";

export const useModelsStore = defineStore("models", () => {
  const phase = ref<LoadPhase>("idle");
  const savePhase = ref<LoadPhase>("idle");
  const catalog = ref<ModelCatalogResponse>();
  const current = ref<ModelConfigurationResponse>();
  const error = ref<UiError>();
  const requests = new RequestOwner();
  const providers = computed(() => catalog.value?.providers ?? []);

  async function load(api: WebApi): Promise<void> {
    const signal = requests.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      const [nextCatalog, nextCurrent] = await Promise.all([
        api.modelCatalog(signal),
        api.currentModel(signal),
      ]);
      if (!requests.owns(signal)) return;
      catalog.value = nextCatalog;
      current.value = nextCurrent;
      phase.value = nextCatalog.providers.length === 0 ? "empty" : "success";
    } catch (caught) {
      if (isCancellation(caught) || !requests.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  async function save(
    api: WebApi,
    request: ModelConfigurationRequest,
  ): Promise<boolean> {
    const signal = requests.next();
    savePhase.value = "loading";
    error.value = undefined;
    try {
      const next = await api.updateModel(request, signal);
      if (!requests.owns(signal)) return false;
      current.value = next;
      savePhase.value = "success";
      return true;
    } catch (caught) {
      if (isCancellation(caught) || !requests.owns(signal)) return false;
      error.value = errorMessage(caught);
      savePhase.value = "error";
      return false;
    }
  }

  return {
    phase,
    savePhase,
    catalog,
    current,
    providers,
    error,
    load,
    save,
    cancel: () => requests.cancel(),
  };
});
