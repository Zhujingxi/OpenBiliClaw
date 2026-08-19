import type { SourceStatus, WebApi } from "../services/api";
import type { components } from "@openbiliclaw/api-client";
import { defineStore } from "pinia";
import { ref } from "vue";
import {
  errorMessage,
  isCancellation,
  RequestOwner,
  type LoadPhase,
} from "./state";

export const useSourcesStore = defineStore("sources", () => {
  const phase = ref<LoadPhase>("idle");
  const items = ref<readonly SourceStatus[]>([]);
  const error = ref<string>();
  const connectPhase = ref<LoadPhase>("idle");
  const connectError = ref<string>();
  const owner = new RequestOwner();
  const connectOwner = new RequestOwner();

  async function load(api: WebApi): Promise<void> {
    const signal = owner.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      const next = await api.listSources(signal);
      if (!owner.owns(signal)) return;
      items.value = next;
      phase.value = next.length === 0 ? "empty" : "success";
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  async function connect(
    api: WebApi,
    command: components["schemas"]["ConnectSourceRequest"],
  ): Promise<void> {
    const signal = connectOwner.next();
    connectPhase.value = "loading";
    connectError.value = undefined;
    try {
      const result = await api.connectSource(command, signal);
      if (!connectOwner.owns(signal)) return;
      items.value = [
        ...items.value.filter(
          (item) => item.provider_id !== result.status.provider_id,
        ),
        result.status,
      ];
      phase.value = "success";
      connectPhase.value = "success";
    } catch (caught) {
      if (isCancellation(caught) || !connectOwner.owns(signal)) return;
      connectError.value = errorMessage(caught);
      connectPhase.value = "error";
    }
  }

  return {
    phase,
    items,
    error,
    connectPhase,
    connectError,
    load,
    connect,
    cancel: () => {
      owner.cancel();
      connectOwner.cancel();
    },
  };
});
