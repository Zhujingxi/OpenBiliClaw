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
  const owner = new RequestOwner();

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
    const signal = owner.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      const result = await api.connectSource(command, signal);
      if (!owner.owns(signal)) return;
      items.value = [
        ...items.value.filter(
          (item) => item.provider_id !== result.status.provider_id,
        ),
        result.status,
      ];
      phase.value = "success";
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  return { phase, items, error, load, connect, cancel: () => owner.cancel() };
});
