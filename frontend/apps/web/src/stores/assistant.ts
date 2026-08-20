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
  type UiError,
} from "./state";

export type AssistantPresentation =
  | "actionPending"
  | "pendingAction"
  | "recommendations"
  | "recommendationsAvailable"
  | "responseUnavailable";

export interface AssistantDisplayMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  presentation?: AssistantPresentation;
  count?: number;
  error?: UiError;
}

type DisplayContent = Pick<
  AssistantDisplayMessage,
  "content" | "presentation" | "count"
>;

export const useAssistantStore = defineStore("assistant", () => {
  const phase = ref<LoadPhase>("idle");
  const conversation = ref<ConversationResponse>();
  const localMessages = ref<readonly AssistantDisplayMessage[]>([]);
  const error = ref<UiError>();
  const owner = new RequestOwner();
  const messages = computed<readonly AssistantDisplayMessage[]>(() => [
    ...(conversation.value?.messages.map((message) => ({
      id: message.message_id,
      role: message.role,
      ...historyContent(message.role, message.content),
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
        { id: `${id}:assistant`, role: "assistant", ...outputContent(next) },
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

function outputContent(response: AssistantTurnResponse): DisplayContent {
  const output = response.output;
  switch (output.kind) {
    case "message":
      return { content: output.text };
    case "clarification":
      return { content: `${output.question} ${output.choices.join(" · ")}` };
    case "recommendations":
      return recommendationContent(output);
    case "pending_action":
      return { content: output.action.effect, presentation: "pendingAction" };
  }
}

function historyContent(
  role: "user" | "assistant" | "tool",
  content: string,
): DisplayContent {
  if (role !== "assistant") return { content };
  let output: unknown;
  try {
    output = JSON.parse(content);
  } catch {
    return { content };
  }
  if (!isRecord(output) || typeof output.kind !== "string")
    return { content: "", presentation: "responseUnavailable" };
  switch (output.kind) {
    case "message":
      return typeof output.text === "string"
        ? { content: output.text }
        : { content: "", presentation: "responseUnavailable" };
    case "recommendations":
      return recommendationContent(output);
    case "clarification": {
      const choices = stringArray(output.choices);
      return typeof output.question === "string"
        ? {
            content: `${output.question}${choices.length ? ` ${choices.join(" · ")}` : ""}`,
          }
        : { content: "", presentation: "responseUnavailable" };
    }
    case "pending_action":
      return isRecord(output.action) && typeof output.action.effect === "string"
        ? { content: output.action.effect, presentation: "pendingAction" }
        : { content: "", presentation: "actionPending" };
    default:
      return { content: "", presentation: "responseUnavailable" };
  }
}

function recommendationContent(
  output: Record<string, unknown>,
): DisplayContent {
  const intro = typeof output.intro === "string" ? output.intro : "";
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
  if (readable.length)
    return intro
      ? { content: `${intro}\n${readable.join("\n")}` }
      : { content: readable.join("\n"), presentation: "recommendations" };
  const count = stringArray(output.recommendation_ids).length;
  if (count)
    return {
      content: intro,
      presentation: "recommendationsAvailable",
      count,
    };
  return intro
    ? { content: intro }
    : { content: "", presentation: "recommendations" };
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
