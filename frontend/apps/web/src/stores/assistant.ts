import type {
  AssistantTurnResponse,
  ConversationResponse,
  WebApi,
} from "../services/api";
import { defineStore } from "pinia";
import { ref } from "vue";
import { errorMessage, RequestOwner, type LoadPhase } from "./state";

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
    phase.value = "loading";
    try {
      conversation.value = await api.conversation(
        conversationId,
        deviceId,
        owner.next(),
      );
      phase.value =
        conversation.value.messages.length === 0 ? "empty" : "success";
    } catch (caught) {
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
    phase.value = "loading";
    error.value = undefined;
    try {
      latest.value = await api.assistantTurn(
        { conversation_id: conversationId, locale: "en-US", text },
        deviceId,
        owner.next(),
      );
      phase.value = "success";
    } catch (caught) {
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
