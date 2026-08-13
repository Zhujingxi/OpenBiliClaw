<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, ref } from "vue";
import { useSourcesStore } from "../stores/sources";
import type { WebApi } from "../services/api";
const providedApi = inject<WebApi>("api");
if (providedApi === undefined) throw new Error("WebApi not provided");
const api: WebApi = providedApi;
const store = useSourcesStore();
const provider = ref("");
const method = ref("default");
const fieldId = ref("credential");
const credential = ref("");
async function connect(): Promise<void> {
  await store.connect(api, {
    provider_id: provider.value,
    method_id: method.value,
    submission: credential.value ? { [fieldId.value]: credential.value } : null,
    idempotency_key: crypto.randomUUID(),
    permissions: ["read_public"],
  });
}
onBeforeUnmount(store.cancel);
</script>
<template>
  <section>
    <h1 tabindex="-1">Connect source</h1>
    <form @submit.prevent="connect">
      <label for="provider-id">Provider</label>
      <input id="provider-id" v-model="provider" autocomplete="off" required />
      <label for="method-id">Connection method</label>
      <input id="method-id" v-model="method" autocomplete="off" required />
      <label for="field-id">Credential field ID</label>
      <input id="field-id" v-model="fieldId" autocomplete="off" required />
      <label for="credential">Credential</label>
      <input
        id="credential"
        v-model="credential"
        type="password"
        autocomplete="off"
      />
      <button type="submit">Connect source</button>
      <p>Credentials are write-only and are never displayed here.</p>
    </form>
    <AsyncState :phase="store.phase" :error="store.error">
      <p aria-live="polite">Source connected.</p>
    </AsyncState>
  </section>
</template>
