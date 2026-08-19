<script setup lang="ts">
import { inject, onBeforeUnmount, onMounted, ref } from "vue";
import { useAssistantStore } from "../stores/assistant";
import { useSessionStore } from "../stores/session";
import type { WebApi } from "../services/api";
const providedApi = inject<WebApi>("api");
if (providedApi === undefined) throw new Error("WebApi not provided");
const api: WebApi = providedApi;
const store = useAssistantStore();
const session = useSessionStore();
const text = ref("");
const CONVERSATION_KEY = "obc-conversation-id";
const CONVERSATION_PATTERN = /^conv_[0-9a-f]{32}$/;
function ensureConversationId(): string {
  const existing = localStorage.getItem(CONVERSATION_KEY);
  if (existing !== null && CONVERSATION_PATTERN.test(existing)) return existing;
  const generated = `conv_${crypto.randomUUID().replaceAll("-", "")}`;
  localStorage.setItem(CONVERSATION_KEY, generated);
  return generated;
}
const conversationId = ref(ensureConversationId());
function plainText(value: string): string {
  return value.replace(/\*\*(.+?)\*\*/g, "$1");
}
function isCapabilityError(value: string): boolean {
  return value.toLowerCase().includes("capability is not configured");
}
onMounted(async () => {
  const found = await store.load(
    api,
    conversationId.value,
    session.deviceId,
    localStorage,
  );
  if (!found) conversationId.value = ensureConversationId();
});
onBeforeUnmount(store.cancel);
async function send(): Promise<void> {
  const message = text.value.trim();
  if (!message) return;
  text.value = "";
  await store.send(api, conversationId.value, session.deviceId, message);
}
</script>
<template>
  <section>
    <h1 tabindex="-1">Assistant</h1>
    <p
      v-if="store.phase === 'loading' && store.messages.length === 0"
      role="status"
      aria-live="polite"
    >
      Loading conversation…
    </p>
    <ol aria-label="Conversation history">
      <li v-for="message in store.messages" :key="message.id">
        <strong>{{ message.role }}</strong>
        <span class="message-content">{{ plainText(message.content) }}</span>
        <p v-if="message.error" role="alert" class="turn-error">
          {{ message.error }}
          <a v-if="isCapabilityError(message.error)" href="#/settings">
            Configure the assistant in Settings.
          </a>
        </p>
      </li>
    </ol>
    <p
      v-if="store.error && !store.messages.some((message) => message.error)"
      role="alert"
      class="turn-error"
    >
      {{ store.error }}
      <a v-if="isCapabilityError(store.error)" href="#/settings">
        Configure the assistant in Settings.
      </a>
    </p>
    <form @submit.prevent="send">
      <label for="assistant-message">Message</label>
      <textarea id="assistant-message" v-model="text" required />
      <button type="submit" :disabled="store.phase === 'loading'">Send</button>
    </form>
  </section>
</template>
<style scoped>
li {
  display: grid;
  gap: 0.25rem;
  margin-block: 0.75rem;
}
.message-content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.turn-error {
  padding: 0.75rem;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: var(--brand-soft);
  color: var(--text-main);
}
.turn-error a {
  color: var(--brand-strong);
}
</style>
