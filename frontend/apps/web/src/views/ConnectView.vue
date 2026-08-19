<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useSourcesStore } from "../stores/sources";
import type { WebApi } from "../services/api";
const providedApi = inject<WebApi>("api");
if (providedApi === undefined) throw new Error("WebApi not provided");
const api: WebApi = providedApi;
const store = useSourcesStore();
const provider = ref("");
const method = ref("builtin.anonymous");
const fieldId = ref("credential");
const credential = ref("");
watch(
  () => store.items,
  (items) => {
    if (!items.some((item) => item.provider_id === provider.value))
      provider.value = items[0]?.provider_id ?? "";
  },
  { immediate: true },
);
onMounted(() => void store.load(api));
async function connect(): Promise<void> {
  await store.connect(api, {
    provider_id: provider.value,
    method_id: method.value,
    submission: credential.value ? { [fieldId.value]: credential.value } : null,
    idempotency_key: crypto.randomUUID(),
    permissions: ["read_public"],
  });
  if (store.connectPhase === "success") credential.value = "";
}
onBeforeUnmount(store.cancel);
</script>
<template>
  <section>
    <h1 tabindex="-1">Connect source</h1>
    <AsyncState :phase="store.phase" :error="store.error">
      <template #empty>
        No source providers are available in this server configuration.
      </template>
      <form v-if="store.items.length" @submit.prevent="connect">
        <label for="provider-id">Provider</label>
        <select id="provider-id" v-model="provider" required>
          <option
            v-for="item in store.items"
            :key="item.provider_id"
            :value="item.provider_id"
          >
            {{ item.provider_id }} — {{ item.state }}
          </option>
        </select>
        <label for="method-id">Connection method</label>
        <select id="method-id" v-model="method" required>
          <option value="builtin.anonymous">Anonymous (no credential)</option>
          <option value="builtin.manual">Credential / cookie</option>
        </select>
        <template v-if="method === 'builtin.manual'">
          <label for="field-id">Credential field ID</label>
          <input id="field-id" v-model="fieldId" autocomplete="off" required />
          <label for="credential">Credential</label>
          <input
            id="credential"
            v-model="credential"
            type="password"
            autocomplete="off"
          />
        </template>
        <button type="submit" :disabled="store.connectPhase === 'loading'">
          {{
            store.connectPhase === "loading" ? "Connecting…" : "Connect source"
          }}
        </button>
        <p>Credentials are write-only and are never displayed here.</p>
        <p
          v-if="store.connectPhase === 'success'"
          role="status"
          aria-live="polite"
        >
          Source connected.
        </p>
        <p v-else-if="store.connectPhase === 'error'" role="alert">
          {{ store.connectError }}
        </p>
      </form>
    </AsyncState>
    <section v-if="store.items.length" aria-labelledby="source-status-heading">
      <h2 id="source-status-heading">Source status</h2>
      <ul aria-label="Source connection statuses" aria-live="polite">
        <li
          v-for="item in store.items"
          :key="`${item.provider_id}:${item.account_id ?? 'default'}`"
          class="source-status"
        >
          <strong>{{ item.provider_id }}</strong>
          <span>{{ item.state }}</span>
        </li>
      </ul>
    </section>
  </section>
</template>
<style scoped>
.source-status {
  display: flex;
  gap: 0.5rem;
}
</style>
