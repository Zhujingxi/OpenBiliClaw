<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, ref } from "vue";
import { useAssistantStore } from "../stores/assistant";
import { useSessionStore } from "../stores/session";
import type { WebApi } from "../services/api";
const providedApi = inject<WebApi>("api");
if (providedApi === undefined) throw new Error("WebApi not provided");
const api: WebApi = providedApi;
const store = useAssistantStore();
const session = useSessionStore();
const text = ref("");
async function send(): Promise<void> {
  const message = text.value;
  text.value = "";
  await store.send(
    api,
    "conv_web0000000000000000000000000000",
    session.deviceId,
    message,
  );
}
</script>
<template>
  <section>
    <h1 tabindex="-1">Assistant</h1>
    <form @submit.prevent="send">
      <label for="assistant-message">Message</label
      ><textarea id="assistant-message" v-model="text" required /><button
        type="submit"
      >
        Send
      </button>
    </form>
    <AsyncState :phase="store.phase" :error="store.error">
      <div aria-live="polite">{{ store.latest?.output.kind }}</div>
    </AsyncState>
  </section>
</template>
