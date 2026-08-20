import type {
  AssistantTurnResponse,
  ConversationResponse,
  WebApi,
} from "../services/api";
import { computed, ref } from "vue";
import { uuid } from "@openbiliclaw/api-client";
import { defineStore } from "pinia";
import type { SupportedLocale } from "../i18n";
import {
  errorMessage,
  isCancellation,
  RequestOwner,
  type LoadPhase,
} from "./state";

export interface AssistantDisplayMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  error?: string;
}

export const useAssistantStore = defineStore("assistant", () => {
  const phase = ref<LoadPhase>("idle");
  const conversation = ref<ConversationResponse>();
  const localMessages = ref<readonly AssistantDisplayMessage[]>([]);
  const error = ref<string>();
  const owner = new RequestOwner();
  const messages = computed<readonly AssistantDisplayMessage[]>(() => [
    ...(conversation.value?.messages.map((message) => ({
      id: message.message_id,
      role: message.role,
      content: historyText(message.role, message.content),
    })) ?? []),
    ...localMessages.value,
  ]);

  async function load(
    api: WebApi,
    conversationId: string,
    deviceId: string,
    storage?: Pick<Storage, "removeItem">,
  ): Promise<boolean> {
    const signal = owner.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      const next = await api.conversation(conversationId, deviceId, signal);
      if (!owner.owns(signal)) return true;
      conversation.value = next;
      localMessages.value = [];
      phase.value = next.messages.length === 0 ? "empty" : "success";
      return true;
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return true;
      if (errorStatus(caught) === 404) {
        storage?.removeItem("obc-conversation-id");
        conversation.value = undefined;
        localMessages.value = [];
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
    locale: SupportedLocale,
  ): Promise<void> {
    const signal = owner.next();
    const id = uuid();
    localMessages.value = [
      ...localMessages.value,
      { id: `${id}:user`, role: "user", content: text },
    ];
    phase.value = "loading";
    error.value = undefined;
    try {
      const next = await api.assistantTurn(
        { conversation_id: conversationId, locale, text },
        deviceId,
        signal,
      );
      if (!owner.owns(signal)) return;
      localMessages.value = [
        ...localMessages.value,
        { id: `${id}:assistant`, role: "assistant", content: outputText(next) },
      ];
      phase.value = "success";
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      const message = errorMessage(caught);
      localMessages.value = localMessages.value.map((item) =>
        item.id === `${id}:user` ? { ...item, error: message } : item,
      );
      error.value = message;
      phase.value = "error";
    }
  }

  return {
    phase,
    conversation,
    messages,
    error,
    load,
    send,
    cancel: () => owner.cancel(),
  };
});

function outputText(response: AssistantTurnResponse): string {
  const output = response.output;
  switch (output.kind) {
    case "message":
      return output.text;
    case "clarification":
      return `${output.question} ${output.choices.join(" · ")}`;
    case "recommendations":
      return recommendationText(output);
    case "pending_action":
      return `Action pending: ${output.action.effect}`;
  }
}

function historyText(
  role: "user" | "assistant" | "tool",
  content: string,
): string {
  if (role !== "assistant") return content;
  let output: unknown;
  try {
    output = JSON.parse(content);
  } catch {
    return content;
  }
  if (!isRecord(output) || typeof output.kind !== "string")
    return "Assistant response unavailable.";
  switch (output.kind) {
    case "message":
      return typeof output.text === "string"
        ? output.text
        : "Assistant response unavailable.";
    case "recommendations":
      return recommendationText(output);
    case "clarification": {
      const choices = stringArray(output.choices);
      return typeof output.question === "string"
        ? `${output.question}${choices.length ? ` ${choices.join(" · ")}` : ""}`
        : "Assistant response unavailable.";
    }
    case "pending_action":
      return isRecord(output.action) && typeof output.action.effect === "string"
        ? `Action pending: ${output.action.effect}`
        : "Action pending.";
    default:
      return "Assistant response unavailable.";
  }
}

function recommendationText(output: Record<string, unknown>): string {
  const intro =
    typeof output.intro === "string" ? output.intro : "Recommendations";
  const items = Array.isArray(output.recommendations)
    ? output.recommendations
    : Array.isArray(output.items)
      ? output.items
      : [];
  const readable = items.flatMap((item) => {
    if (!isRecord(item) || typeof item.title !== "string") return [];
    const url =
      typeof item.canonical_url === "string"
        ? item.canonical_url
        : typeof item.url === "string"
          ? item.url
          : undefined;
    return [`${item.title}${url ? ` — ${url}` : ""}`];
  });
  if (readable.length) return `${intro}\n${readable.join("\n")}`;
  const count = stringArray(output.recommendation_ids).length;
  return count
    ? `${intro} ${count} recommendations are available in your feed.`
    : intro;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function errorStatus(value: unknown): number | undefined {
  if (typeof value !== "object" || value === null || !("status" in value))
    return undefined;
  return typeof value.status === "number" ? value.status : undefined;
}
