<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { computed, inject, onBeforeUnmount, onMounted, ref } from "vue";
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
const latestText = computed(() => {
  const output = store.latest?.output;
  if (output === undefined) return undefined;
  switch (output.kind) {
    case "message":
      return output.text;
    case "clarification":
      return `${output.question} ${output.choices.join(" · ")}`;
    case "recommendations":
      return `${output.intro} ${output.recommendation_ids.join(" · ")}`;
    case "pending_action":
      return `Action pending: ${output.action.effect}`;
    default:
      return undefined;
  }
});
function plainText(value: string): string {
  return value.replace(/\*\*(.+?)\*\*/g, "$1");
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
  const message = text.value;
  text.value = "";
  await store.send(api, conversationId.value, session.deviceId, message);
}
</script>
<template>
  <section>
    <h1 tabindex="-1">Assistant</h1>
    <ol aria-label="Conversation history">
      <li
        v-for="message in store.conversation?.messages ?? []"
        :key="message.message_id"
      >
        <strong>{{ message.role }}</strong>
        <span class="message-content">{{ plainText(message.content) }}</span>
      </li>
      <li v-if="store.latestUserText">
        <strong>user</strong>
        <span class="message-content">{{ store.latestUserText }}</span>
      </li>
    </ol>
    <form @submit.prevent="send">
      <label for="assistant-message">Message</label>
      <textarea id="assistant-message" v-model="text" required />
      <button type="submit">Send</button>
    </form>
    <AsyncState :phase="store.phase" :error="store.error">
      <div class="message-content" aria-live="polite">
        {{ latestText ? plainText(latestText) : "" }}
      </div>
    </AsyncState>
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
</style>
