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
  const error = ref<string>();
  const owner = new RequestOwner();

  async function load(
    api: WebApi,
    conversationId: string,
    deviceId: string,
  ): Promise<void> {
    const signal = owner.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      const next = await api.conversation(conversationId, deviceId, signal);
      if (!owner.owns(signal)) return;
      conversation.value = next;
      phase.value = next.messages.length === 0 ? "empty" : "success";
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  async function send(
    api: WebApi,
    conversationId: string,
    deviceId: string,
    text: string,
  ): Promise<void> {
    const signal = owner.next();
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
    error,
    load,
    send,
    cancel: () => owner.cancel(),
  };
});
