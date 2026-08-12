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
const conversationId = "conv_web0000000000000000000000000000";
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
onMounted(() => store.load(api, conversationId, session.deviceId));
onBeforeUnmount(store.cancel);
async function send(): Promise<void> {
  const message = text.value;
  text.value = "";
  await store.send(api, conversationId, session.deviceId, message);
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
        <strong>{{ message.role }}</strong> {{ message.content }}
      </li>
    </ol>
    <form @submit.prevent="send">
      <label for="assistant-message">Message</label>
      <textarea id="assistant-message" v-model="text" required />
      <button type="submit">Send</button>
    </form>
    <AsyncState :phase="store.phase" :error="store.error">
      <div aria-live="polite">{{ latestText }}</div>
    </AsyncState>
  </section>
</template>
