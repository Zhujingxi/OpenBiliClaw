<script setup lang="ts">
import { CardRenderer, type CardView } from "@openbiliclaw/presentation";
import { storeToRefs } from "pinia";
import { ref } from "vue";
import { useConnectionStore } from "./connection-store";

const store = useConnectionStore();
store.hydrate();
const { backendUrl, deviceToken, state, error } = storeToRefs(store);
const connectionCard: CardView = {
  version: 1,
  kind: "fallback",
  providerLabel: "OpenBiliClaw",
  availability: "available",
  data: {
    badge: "Local",
    image_url: null,
    provenance: {
      native_schema_version: 1,
      projected_at: new Date(0).toISOString(),
      ref: {
        provider_id: { value: "bilibili" },
        provider_content_id: "runtime",
        content_kind: { value: "article" },
        canonical_url: "http://127.0.0.1:8420/",
      },
    },
    ref: {
      provider_id: { value: "bilibili" },
      provider_content_id: "runtime",
      content_kind: { value: "article" },
      canonical_url: "http://127.0.0.1:8420/",
    },
    source_timestamp: new Date(0).toISOString(),
    summary: "Connect this extension to the local OpenBiliClaw backend.",
    title: "Local companion",
  },
};
const draftUrl = ref(backendUrl.value);
const draftToken = ref(deviceToken.value);
const formError = ref<string>();
function save(): void {
  try {
    store.configure(draftUrl.value, draftToken.value);
    formError.value = undefined;
    void store.check();
  } catch (caught: unknown) {
    formError.value =
      caught instanceof Error ? caught.message : "Invalid connection settings";
  }
}
</script>

<template>
  <main aria-labelledby="extension-title" class="shell">
    <header>
      <h1 id="extension-title">OpenBiliClaw</h1>
      <p>Local recommendation companion</p>
    </header>
    <CardRenderer :card="connectionCard" />
    <section aria-labelledby="connection-title">
      <h2 id="connection-title">Backend connection</h2>
      <form @submit.prevent="save">
        <label>
          Backend URL
          <input
            v-model="draftUrl"
            name="backendUrl"
            aria-label="Backend URL"
            inputmode="url"
            autocomplete="url"
          />
        </label>
        <label>
          Device token
          <input
            v-model="draftToken"
            name="deviceToken"
            aria-label="Device token"
            type="password"
            autocomplete="off"
          />
        </label>
        <button type="submit">Save and connect</button>
      </form>
      <p role="status" aria-live="polite">Status: {{ state }}</p>
      <p v-if="formError ?? error" role="alert">{{ formError ?? error }}</p>
    </section>
  </main>
</template>

<style scoped>
:global(*) {
  box-sizing: border-box;
}
:global(body) {
  margin: 0;
  color: #172033;
  background: #f5f7fb;
  font:
    14px/1.5 system-ui,
    sans-serif;
}
.shell {
  min-width: 320px;
  max-width: 520px;
  padding: 1rem;
}
header,
section {
  background: white;
  border-radius: 12px;
  padding: 1rem;
  margin-bottom: 0.75rem;
  box-shadow: 0 1px 4px #1720331f;
}
h1,
h2 {
  margin: 0 0 0.5rem;
}
p {
  margin: 0.25rem 0;
}
label {
  display: grid;
  gap: 0.25rem;
  margin-block: 0.75rem;
  font-weight: 600;
}
input,
button {
  min-height: 44px;
  border: 1px solid #79839a;
  border-radius: 8px;
  padding: 0.6rem;
}
button {
  color: white;
  background: #3156d3;
  font-weight: 700;
  cursor: pointer;
}
button:focus-visible,
input:focus-visible {
  outline: 3px solid #ffb000;
  outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
  * {
    scroll-behavior: auto !important;
  }
}
</style>
