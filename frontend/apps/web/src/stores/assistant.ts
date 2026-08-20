import { ApiError, uuid } from "@openbiliclaw/api-client";
import { computed, ref } from "vue";
import { defineStore } from "pinia";
import type { SupportedLocale } from "../i18n";
import type {
  AssistantLifecycleEvent,
  AssistantTurnResponse,
  ConversationResponse,
  WebApi,
} from "../services/api";
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
  toolCalls?: readonly AssistantToolCard[];
}

export interface AssistantReasoning {
  readonly text: string;
  readonly active: boolean;
}

export interface AssistantToolCard {
  readonly id: string;
  readonly name: string;
  readonly summary: string;
  readonly status: "running" | "succeeded" | "failed";
}

type ContextMeter = Extract<
  AssistantLifecycleEvent,
  { kind: "turn_started" }
>["context_meter"];
type DisplayContent = Pick<
  AssistantDisplayMessage,
  "content" | "presentation" | "count"
>;
type TurnStage = "awaiting-start" | "active" | "terminal";

export const useAssistantStore = defineStore("assistant", () => {
  const phase = ref<LoadPhase>("idle");
  const conversation = ref<ConversationResponse>();
  const localMessages = ref<readonly AssistantDisplayMessage[]>([]);
  const reasoning = ref<AssistantReasoning>();
  const tools = ref<readonly AssistantToolCard[]>([]);
  const contextMeter = ref<ContextMeter>();
  const error = ref<UiError>();
  const activeTurnId = ref<string>();
  const owner = new RequestOwner();
  const messages = computed<readonly AssistantDisplayMessage[]>(() => [
    ...(conversation.value?.messages.map((message) => ({
      id: message.message_id,
      role: message.role,
      ...historyContent(message.role, message.content),
      ...(message.role === "assistant"
        ? {
            toolCalls: message.tool_calls.flatMap((tool, index) =>
              tool.outcome === "pending"
                ? []
                : [
                    {
                      id: `${message.message_id}:tool:${index}`,
                      name: tool.tool_name,
                      summary: tool.safe_summary,
                      status: tool.outcome,
                    },
                  ],
            ),
          }
        : {}),
    })) ?? []),
    ...localMessages.value,
  ]);
  const isRunning = computed(() => activeTurnId.value !== undefined);

  async function load(
    api: WebApi,
    conversationId: string,
    deviceId: string,
    storage?: Pick<Storage, "removeItem">,
  ): Promise<boolean> {
    const signal = owner.next();
    clearTransient();
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
    activeTurnId.value = id;
    reasoning.value = undefined;
    tools.value = [];
    error.value = undefined;
    localMessages.value = [
      ...localMessages.value,
      { id: `${id}:user`, role: "user", content: text },
    ];
    phase.value = "loading";
    let stage: TurnStage = "awaiting-start";
    let responseStarted = false;
    try {
      for await (const event of api.assistantTurnStream(
        { conversation_id: conversationId, locale, text },
        deviceId,
        signal,
      )) {
        if (!owner.owns(signal)) return;
        if (event.kind === "error") {
          failTurn(id, streamError(event.code));
          stage = "terminal";
          break;
        }
        if (event.kind === "turn_started") {
          if (stage !== "awaiting-start") invalidOrder();
          contextMeter.value = event.context_meter;
          stage = "active";
          continue;
        }
        if (stage !== "active") invalidOrder();
        switch (event.kind) {
          case "reasoning_started": {
            const current = reasoning.value as AssistantReasoning | undefined;
            if (current?.active) invalidOrder();
            reasoning.value = { text: current?.text ?? "", active: true };
            break;
          }
          case "reasoning_delta": {
            const current = reasoning.value as AssistantReasoning | undefined;
            if (!current?.active) invalidOrder();
            reasoning.value = {
              text: current.text + event.delta,
              active: true,
            };
            break;
          }
          case "reasoning_finished": {
            const current = reasoning.value as AssistantReasoning | undefined;
            if (!current?.active) invalidOrder();
            reasoning.value = { ...current, active: false };
            break;
          }
          case "tool_started":
            tools.value = [
              ...tools.value,
              {
                id: `${id}:tool:${tools.value.length}`,
                name: event.name,
                summary: "",
                status: "running",
              },
            ];
            break;
          case "tool_finished": {
            const index = findRunningTool(event.name, tools.value);
            if (index < 0) invalidOrder();
            tools.value = tools.value.map((tool, toolIndex) =>
              toolIndex === index
                ? {
                    ...tool,
                    summary: event.summary,
                    status: event.status,
                  }
                : tool,
            );
            break;
          }
          case "response_delta":
            if (reasoning.value?.active) invalidOrder();
            responseStarted = true;
            appendResponseDelta(id, event.delta);
            break;
          case "turn_finished": {
            if (
              reasoning.value?.active ||
              tools.value.some((tool) => tool.status === "running")
            )
              invalidOrder();
            contextMeter.value = event.context_meter;
            if (!responseStarted) {
              localMessages.value = [
                ...localMessages.value,
                {
                  id: `${id}:assistant`,
                  role: "assistant",
                  ...outputContent({ output: event.output }),
                },
              ];
            }
            const currentReasoning = reasoning.value as
              | AssistantReasoning
              | undefined;
            reasoning.value = currentReasoning
              ? { ...currentReasoning, active: false }
              : undefined;
            phase.value = "success";
            stage = "terminal";
            break;
          }
        }
        if (stage === "terminal") break;
      }
      if (stage !== "terminal") invalidOrder();
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      failTurn(id, errorMessage(caught));
    } finally {
      if (activeTurnId.value === id) activeTurnId.value = undefined;
    }
  }

  function appendResponseDelta(id: string, delta: string): void {
    const messageId = `${id}:assistant`;
    const index = localMessages.value.findIndex(
      (message) => message.id === messageId,
    );
    if (index < 0) {
      localMessages.value = [
        ...localMessages.value,
        { id: messageId, role: "assistant", content: delta },
      ];
      return;
    }
    localMessages.value = localMessages.value.map((message, messageIndex) =>
      messageIndex === index
        ? { ...message, content: message.content + delta }
        : message,
    );
  }

  function stop(): void {
    if (activeTurnId.value === undefined) {
      owner.cancel();
      return;
    }
    const id = activeTurnId.value;
    owner.cancel();
    activeTurnId.value = undefined;
    localMessages.value = localMessages.value.filter(
      (message) => message.id !== `${id}:assistant`,
    );
    reasoning.value = undefined;
    tools.value = [];
    contextMeter.value = undefined;
    error.value = undefined;
    phase.value = messages.value.length === 0 ? "empty" : "idle";
  }

  function newChat(): void {
    owner.cancel();
    activeTurnId.value = undefined;
    conversation.value = undefined;
    localMessages.value = [];
    clearTransient();
    error.value = undefined;
    phase.value = "empty";
  }

  function clearTransient(): void {
    activeTurnId.value = undefined;
    reasoning.value = undefined;
    tools.value = [];
    contextMeter.value = undefined;
  }

  function failTurn(id: string, message: UiError): void {
    localMessages.value = localMessages.value
      .filter((item) => item.id !== `${id}:assistant`)
      .map((item) =>
        item.id === `${id}:user` ? { ...item, error: message } : item,
      );
    reasoning.value = reasoning.value
      ? { ...reasoning.value, active: false }
      : undefined;
    tools.value = tools.value.map((tool) =>
      tool.status === "running" ? { ...tool, status: "failed" } : tool,
    );
    error.value = message;
    phase.value = "error";
  }

  return {
    phase,
    conversation,
    messages,
    reasoning,
    tools,
    contextMeter,
    error,
    isRunning,
    load,
    send,
    stop,
    newChat,
    cancel: stop,
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

function findRunningTool(
  name: string,
  tools: readonly AssistantToolCard[],
): number {
  for (let index = tools.length - 1; index >= 0; index -= 1) {
    const tool = tools[index];
    if (tool?.name === name && tool.status === "running") return index;
  }
  return -1;
}

function streamError(code: "unavailable" | "temporary_failure"): UiError {
  return {
    key:
      code === "unavailable" ? "errors.unavailable" : "errors.temporaryFailure",
    code,
    status: 503,
  };
}

function invalidOrder(): never {
  throw new ApiError(
    "invalid-response",
    "Assistant lifecycle events were out of order",
  );
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
