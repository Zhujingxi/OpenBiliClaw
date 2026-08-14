import type {
  AssistantTurnResponse,
  ConversationResponse,
  WebApi,
} from "../services/api";
import { defineStore } from "pinia";
import { ref } from "vue";
import {
  errorMessage,
  isCancellation,
  RequestOwner,
  type LoadPhase,
} from "./state";

export const useAssistantStore = defineStore("assistant", () => {
  const phase = ref<LoadPhase>("idle");
  const conversation = ref<ConversationResponse>();
  const latest = ref<AssistantTurnResponse>();
  const latestUserText = ref<string>();
  const error = ref<string>();
  const owner = new RequestOwner();

  async function load(
    api: WebApi,
    conversationId: string,
    deviceId: string,
    storage?: Pick<Storage, "removeItem">,
  ): Promise<boolean> {
    const signal = owner.next();
    conversation.value = undefined;
    latest.value = undefined;
    latestUserText.value = undefined;
    phase.value = "loading";
    error.value = undefined;
    try {
      const next = await api.conversation(conversationId, deviceId, signal);
      if (!owner.owns(signal)) return true;
      conversation.value = next;
      phase.value = next.messages.length === 0 ? "empty" : "success";
      return true;
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return true;
      if (errorStatus(caught) === 404) {
        storage?.removeItem("obc-conversation-id");
        conversation.value = undefined;
        latest.value = undefined;
        phase.value = "empty";
        return false;
      }
      error.value = errorMessage(caught);
      phase.value = "error";
      return true;
    }
  }

  async function send(
    api: WebApi,
    conversationId: string,
    deviceId: string,
    text: string,
  ): Promise<void> {
    const signal = owner.next();
    latestUserText.value = text;
    phase.value = "loading";
    error.value = undefined;
    try {
      const next = await api.assistantTurn(
        { conversation_id: conversationId, locale: "en-US", text },
        deviceId,
        signal,
      );
      if (!owner.owns(signal)) return;
      latest.value = next;
      phase.value = "success";
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  return {
    phase,
    conversation,
    latest,
    latestUserText,
    error,
    load,
    send,
    cancel: () => owner.cancel(),
  };
});

function errorStatus(value: unknown): number | undefined {
  if (typeof value !== "object" || value === null || !("status" in value))
    return undefined;
  return typeof value.status === "number" ? value.status : undefined;
}
