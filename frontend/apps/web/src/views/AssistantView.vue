<script setup lang="ts">
import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { useI18n } from "vue-i18n";
import { useLocale } from "../i18n";
import { uuid } from "@openbiliclaw/api-client";
import {
  useAssistantStore,
  type AssistantDisplayMessage,
} from "../stores/assistant";
import type { UiError } from "../stores/state";
import LocalizedError from "../components/LocalizedError.vue";
import { useSessionStore } from "../stores/session";
import type { WebApi } from "../services/api";

const providedApi = inject<WebApi>("api");
if (providedApi === undefined) throw new Error("WebApi not provided");
const api: WebApi = providedApi;
const { t } = useI18n();
const { locale } = useLocale();
const store = useAssistantStore();
const session = useSessionStore();
const text = ref("");
const transcript = ref<HTMLElement>();
const suggestions = computed(() => [
  t("assistant.suggestion1"),
  t("assistant.suggestion2"),
]);
const contextPercent = computed(() =>
  Math.min(
    100,
    Math.max(0, Math.round(store.contextMeter?.approximate_usage_percent ?? 0)),
  ),
);
const CONVERSATION_KEY = "obc-conversation-id";
const CONVERSATION_PATTERN = /^conv_[0-9a-f]{32}$/;
function ensureConversationId(): string {
  const existing = localStorage.getItem(CONVERSATION_KEY);
  if (existing !== null && CONVERSATION_PATTERN.test(existing)) return existing;
  const generated = `conv_${uuid().replaceAll("-", "")}`;
  localStorage.setItem(CONVERSATION_KEY, generated);
  return generated;
}
const conversationId = ref(ensureConversationId());
function plainText(value: string): string {
  return value.replace(/\*\*(.+?)\*\*/g, "$1");
}
function messageText(message: AssistantDisplayMessage): string {
  switch (message.presentation) {
    case "responseUnavailable":
      return t("assistant.responseUnavailable");
    case "pendingAction":
      return t("assistant.pendingAction", { effect: message.content });
    case "actionPending":
      return t("assistant.actionPending");
    case "recommendations":
      return message.content
        ? `${t("assistant.recommendations")}\n${message.content}`
        : t("assistant.recommendations");
    case "recommendationsAvailable":
      return `${message.content || t("assistant.recommendations")} ${t(
        "assistant.recommendationsAvailable",
        { count: message.count ?? 0 },
      )}`;
    default:
      return message.content;
  }
}
function isCapabilityError(value: UiError): boolean {
  return (
    (value.code === "unavailable_capability" || value.code === "unavailable") &&
    value.status === 503
  );
}
async function scrollToLatest(): Promise<void> {
  await nextTick();
  if (transcript.value)
    transcript.value.scrollTop = transcript.value.scrollHeight;
}
onMounted(async () => {
  const found = await store.load(
    api,
    conversationId.value,
    session.deviceId,
    localStorage,
  );
  if (!found) conversationId.value = ensureConversationId();
  await scrollToLatest();
});
onBeforeUnmount(store.cancel);
watch(
  () => [
    store.messages.map((message) => message.content).join(""),
    store.reasoning?.text,
    store.tools.length,
  ],
  scrollToLatest,
);
async function send(message = text.value): Promise<void> {
  const next = message.trim();
  if (!next || store.isRunning) return;
  text.value = "";
  await store.send(
    api,
    conversationId.value,
    session.deviceId,
    next,
    locale.value,
  );
}
function newChat(): void {
  store.newChat();
  text.value = "";
  conversationId.value = `conv_${uuid().replaceAll("-", "")}`;
  localStorage.setItem(CONVERSATION_KEY, conversationId.value);
}
</script>

<template>
  <section class="assistant-page">
    <div class="chat-window">
      <header class="chat-header">
        <div class="assistant-identity">
          <span class="assistant-avatar" aria-hidden="true">✦</span>
          <div>
            <h1 tabindex="-1">{{ t("assistant.title") }}</h1>
            <p>
              <span class="online-dot" aria-hidden="true"></span>
              {{ t("assistant.localProfile") }}
            </p>
          </div>
        </div>
        <div class="header-actions">
          <button type="button" @click="newChat">
            {{ t("assistant.newChat") }}
          </button>
          <a href="#/settings">{{ t("assistant.settings") }}</a>
        </div>
      </header>

      <div ref="transcript" class="chat-transcript">
        <div
          v-if="store.messages.length === 0 && store.phase !== 'loading'"
          class="chat-welcome"
        >
          <span class="welcome-mark" aria-hidden="true">✦</span>
          <h2>{{ t("assistant.promptTitle") }}</h2>
          <p>{{ t("assistant.promptIntro") }}</p>
          <div class="suggestion-list" :aria-label="t('assistant.suggestions')">
            <button
              v-for="suggestion in suggestions"
              :key="suggestion"
              type="button"
              @click="send(suggestion)"
            >
              {{ suggestion }}
            </button>
          </div>
        </div>

        <p
          v-if="store.phase === 'loading' && store.messages.length === 0"
          class="loading-conversation"
          role="status"
          aria-live="polite"
        >
          {{ t("assistant.loading") }}
        </p>

        <div v-if="store.contextMeter" class="context-status" role="status">
          <strong>{{
            t("assistant.contextMeter", { percent: contextPercent })
          }}</strong>
          <span v-if="store.contextMeter.excluded_oldest_turns > 0">
            {{
              t("assistant.contextExcluded", {
                count: store.contextMeter.excluded_oldest_turns,
              })
            }}
          </span>
        </div>

        <ol :aria-label="t('assistant.history')">
          <li
            v-for="message in store.messages"
            :key="message.id"
            :class="[
              `message-${message.role}`,
              { 'message-failed': message.error },
            ]"
          >
            <span class="message-avatar" aria-hidden="true">
              {{
                message.role === "user"
                  ? "Y"
                  : message.role === "tool"
                    ? "T"
                    : "✦"
              }}
            </span>
            <div class="message-body">
              <strong>{{
                t(
                  `assistant.role${message.role.charAt(0).toUpperCase()}${message.role.slice(1)}`,
                )
              }}</strong>
              <span class="message-content">{{
                plainText(messageText(message))
              }}</span>
              <p v-if="message.error" role="alert" class="turn-error">
                <LocalizedError :error="message.error" />
                <a v-if="isCapabilityError(message.error)" href="#/settings">
                  {{ t("assistant.configure") }}
                </a>
              </p>
            </div>
          </li>
        </ol>

        <details
          v-if="store.reasoning?.text"
          class="reasoning-card"
          :open="store.reasoning.active || undefined"
        >
          <summary>
            {{
              store.reasoning.active
                ? t("assistant.reasoningLive")
                : t("assistant.reasoning")
            }}
          </summary>
          <p>{{ store.reasoning.text }}</p>
        </details>

        <section
          v-if="store.tools.length"
          class="tool-cards"
          :aria-label="t('assistant.tools')"
        >
          <article v-for="tool in store.tools" :key="tool.id" class="tool-card">
            <span class="tool-status-icon" aria-hidden="true">{{
              tool.status === "running"
                ? "…"
                : tool.status === "succeeded"
                  ? "✓"
                  : "!"
            }}</span>
            <div>
              <strong>{{ tool.name }}</strong>
              <p v-if="tool.summary">{{ tool.summary }}</p>
              <span class="tool-status-text">{{
                t(
                  `assistant.tool${tool.status.charAt(0).toUpperCase()}${tool.status.slice(1)}`,
                )
              }}</span>
            </div>
          </article>
        </section>

        <div
          v-if="store.isRunning && store.messages.length > 0"
          class="typing-row"
          role="status"
          aria-live="polite"
        >
          <span class="message-avatar" aria-hidden="true">✦</span>
          <span class="typing-indicator" :aria-label="t('assistant.thinking')">
            <i></i><i></i><i></i>
          </span>
        </div>

        <p
          v-if="store.error && !store.messages.some((message) => message.error)"
          role="alert"
          class="turn-error global-error"
        >
          <LocalizedError :error="store.error" />
          <a v-if="isCapabilityError(store.error)" href="#/settings">
            {{ t("assistant.configure") }}
          </a>
        </p>
      </div>

      <form class="chat-composer" @submit.prevent="send()">
        <label class="visually-hidden" for="assistant-message">{{
          t("assistant.message")
        }}</label>
        <textarea
          id="assistant-message"
          v-model="text"
          required
          rows="1"
          :placeholder="t('assistant.placeholder')"
          @keydown.enter.exact.prevent="send()"
        />
        <button
          v-if="store.isRunning"
          type="button"
          class="stop-button"
          :aria-label="t('assistant.stop')"
          @click="store.stop"
        >
          <span class="send-label">{{ t("assistant.stop") }}</span>
          <span aria-hidden="true">■</span>
        </button>
        <button
          v-else
          type="submit"
          :disabled="!text.trim()"
          :aria-label="t('assistant.send')"
        >
          <span class="send-label">{{ t("assistant.send") }}</span>
          <span aria-hidden="true">↑</span>
        </button>
        <p>{{ t("assistant.composerHelp") }}</p>
      </form>
    </div>
  </section>
</template>

<style scoped>
.assistant-page {
  height: calc(100dvh - 4rem - clamp(2.3rem, 6vw, 4rem));
  min-height: 32rem;
}
.chat-window {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  min-height: 100%;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--card);
  box-shadow: var(--shadow-sm);
}
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border-bottom: 1px solid var(--border);
  padding: 0.9rem 1rem;
  background: rgb(255 254 250 / 0.92);
}
.assistant-identity {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.assistant-avatar,
.message-avatar,
.welcome-mark {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 0.7rem;
  background: var(--primary);
  color: var(--primary-foreground);
  font-size: 0.78rem;
  font-weight: 800;
}
.assistant-avatar {
  width: 2.25rem;
  height: 2.25rem;
}
.chat-header h1 {
  font-size: 0.95rem;
  letter-spacing: -0.01em;
}
.chat-header p {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin: 0.1rem 0 0;
  color: var(--muted-foreground);
  font-size: 0.68rem;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 0.45rem;
}
.header-actions > a,
.header-actions > button {
  min-height: 2.1rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.38rem 0.65rem;
  background: var(--card);
  color: var(--foreground);
  font-size: 0.7rem;
  font-weight: 650;
  text-decoration: none;
}
.online-dot {
  width: 0.4rem;
  height: 0.4rem;
  border-radius: 50%;
  background: var(--success);
}
.chat-transcript {
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: clamp(1rem, 3vw, 2rem);
  background:
    linear-gradient(rgb(255 254 250 / 0.76), rgb(255 254 250 / 0.76)),
    radial-gradient(circle at 20% 20%, var(--brand-soft), transparent 26rem);
  scrollbar-gutter: stable;
}
.chat-welcome {
  display: grid;
  justify-items: center;
  max-width: 38rem;
  margin: min(9vh, 5rem) auto 2rem;
  text-align: center;
}
.welcome-mark {
  width: 3rem;
  height: 3rem;
  margin-bottom: 1rem;
  border-radius: 1rem;
  box-shadow: 0 10px 28px rgb(31 30 27 / 0.15);
}
.chat-welcome h2 {
  font-size: 1.35rem;
  letter-spacing: -0.025em;
}
.chat-welcome > p {
  max-width: 32rem;
  margin-bottom: 1.25rem;
  color: var(--muted-foreground);
  font-size: 0.85rem;
}
.suggestion-list {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 0.5rem;
}
.suggestion-list button {
  min-height: 2.2rem;
  border-color: var(--border);
  border-radius: 999px;
  background: var(--card);
  color: var(--foreground);
  font-size: 0.72rem;
  font-weight: 580;
  box-shadow: var(--shadow-sm);
}
.suggestion-list button:hover:not(:disabled) {
  border-color: var(--brand);
  background: var(--brand-soft);
}
.loading-conversation {
  margin: 2rem auto;
  text-align: center;
}
.context-status,
.reasoning-card,
.tool-cards {
  max-width: 54rem;
  margin: 0 auto 1rem;
}
.context-status {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 0.65rem;
  color: var(--muted-foreground);
  font-size: 0.7rem;
}
.context-status strong {
  color: var(--foreground);
}
.reasoning-card {
  margin-top: 1rem;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 0.65rem 0.8rem;
  background: var(--muted);
  color: var(--muted-foreground);
  font-size: 0.75rem;
}
.reasoning-card summary {
  cursor: pointer;
  color: var(--foreground);
  font-weight: 700;
}
.reasoning-card p {
  margin: 0.55rem 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.tool-cards {
  display: grid;
  gap: 0.5rem;
  margin-top: 1rem;
}
.tool-card {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 0.6rem;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 0.65rem 0.8rem;
  background: var(--card);
  font-size: 0.75rem;
}
.tool-status-icon {
  display: grid;
  place-items: center;
  width: 1.4rem;
  height: 1.4rem;
  border-radius: 50%;
  background: var(--muted);
  font-weight: 800;
}
.tool-card p {
  margin: 0.2rem 0;
  color: var(--muted-foreground);
  overflow-wrap: anywhere;
}
.tool-status-text {
  color: var(--muted-foreground);
  font-size: 0.68rem;
}
ol {
  display: grid;
  gap: 1rem;
  max-width: 54rem;
  margin: 0 auto;
  padding: 0;
  list-style: none;
}
li,
.typing-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 0.65rem;
  align-items: start;
}
li.message-user {
  grid-template-columns: minmax(0, 1fr) auto;
}
li.message-user .message-avatar {
  grid-column: 2;
}
li.message-user .message-body {
  grid-column: 1;
  grid-row: 1;
  justify-self: end;
  border-color: var(--primary);
  background: var(--primary);
  color: var(--primary-foreground);
  border-bottom-right-radius: 0.3rem;
}
.message-avatar {
  width: 1.85rem;
  height: 1.85rem;
  border-radius: 0.55rem;
  font-size: 0.64rem;
}
.message-user .message-avatar {
  background: var(--brand);
}
.message-tool .message-avatar {
  background: var(--muted);
  color: var(--muted-foreground);
}
.message-body {
  display: grid;
  gap: 0.3rem;
  width: fit-content;
  max-width: min(44rem, 88%);
  border: 1px solid var(--border);
  border-radius: 0.9rem;
  border-bottom-left-radius: 0.3rem;
  padding: 0.7rem 0.85rem;
  background: var(--card);
  box-shadow: var(--shadow-sm);
}
.message-body > strong {
  color: var(--muted-foreground);
  font-size: 0.62rem;
  font-weight: 750;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.message-user .message-body > strong {
  color: rgb(255 254 250 / 0.65);
  text-align: right;
}
.message-content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 0.86rem;
  line-height: 1.58;
}
.turn-error {
  margin: 0.45rem 0 0;
  border-color: color-mix(in srgb, var(--error) 28%, transparent);
  background: #fff5f4;
  color: var(--error);
}
.global-error {
  max-width: 54rem;
  margin: 1rem auto 0;
}
.typing-row {
  max-width: 54rem;
  margin: 1rem auto 0;
}
.typing-indicator {
  display: flex;
  gap: 0.25rem;
  width: fit-content;
  border: 1px solid var(--border);
  border-radius: 0.9rem;
  border-bottom-left-radius: 0.3rem;
  padding: 0.75rem 0.9rem;
  background: var(--card);
}
.typing-indicator i {
  width: 0.35rem;
  height: 0.35rem;
  border-radius: 50%;
  background: var(--muted-foreground);
  animation: pulse 1.2s infinite ease-in-out;
}
.typing-indicator i:nth-child(2) {
  animation-delay: 120ms;
}
.typing-indicator i:nth-child(3) {
  animation-delay: 240ms;
}
.chat-composer {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.55rem;
  border-top: 1px solid var(--border);
  padding: 0.8rem 1rem 0.65rem;
  background: var(--card);
}
.chat-composer textarea {
  min-height: 2.8rem;
  max-height: 8rem;
  border-radius: 0.85rem;
  padding: 0.72rem 0.8rem;
  font-size: 16px;
  line-height: 1.35;
}
.chat-composer button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.45rem;
  min-width: 5rem;
  border-radius: 0.85rem;
}
.chat-composer .stop-button {
  border-color: var(--error);
  background: var(--card);
  color: var(--error);
}
.chat-composer > p {
  grid-column: 1 / -1;
  margin: 0;
  padding-left: 0.2rem;
  color: var(--muted-foreground);
  font-size: 0.62rem;
}
.visually-hidden {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  padding: 0 !important;
  overflow: hidden !important;
  clip: rect(0, 0, 0, 0) !important;
  white-space: nowrap !important;
  border: 0 !important;
}
@keyframes pulse {
  0%,
  60%,
  100% {
    opacity: 0.35;
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-2px);
  }
}
@media (max-width: 48rem) {
  .assistant-page {
    height: calc(100dvh - 3.5rem - 2rem - 4.6rem - var(--safe-bottom));
    min-height: 28rem;
  }
  .header-actions > a {
    display: none;
  }
  .chat-transcript {
    padding: 1rem 0.75rem;
  }
  .message-body {
    max-width: 94%;
  }
  .chat-composer {
    padding-inline: 0.75rem;
  }
  .send-label {
    display: none;
  }
  .chat-composer button {
    min-width: 2.8rem;
    width: 2.8rem;
    padding: 0;
  }
}
</style>
