<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { uuid } from "@openbiliclaw/api-client";
import { inject, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { routeParameter } from "../app/routes";
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
onMounted(() => {
  void store.load(api).then(() => {
    const requested = routeParameter(location.hash);
    if (requested && store.items.some((item) => item.provider_id === requested))
      provider.value = requested;
  });
});
async function connect(): Promise<void> {
  await store.connect(api, {
    provider_id: provider.value,
    method_id: method.value,
    submission: credential.value ? { [fieldId.value]: credential.value } : null,
    idempotency_key: uuid(),
    permissions: ["read_public"],
  });
  if (store.connectPhase === "success") credential.value = "";
}
onBeforeUnmount(store.cancel);
</script>

<template>
  <section class="connect-page">
    <div class="page-heading">
      <div class="page-heading-copy">
        <p class="eyebrow">Source setup</p>
        <h1 tabindex="-1">Connect content source</h1>
        <p>
          Choose a provider, its access method, then supply only what it needs.
        </p>
      </div>
      <a class="back-link" href="#/providers">← All content sources</a>
    </div>

    <AsyncState :phase="store.phase" :error="store.error">
      <template #empty>
        No source providers are available in this server configuration.
      </template>
      <form
        v-if="store.items.length"
        class="connection-form"
        @submit.prevent="connect"
      >
        <div class="setup-step">
          <span class="step-number">1</span>
          <div class="step-content">
            <div class="surface-card-header">
              <div>
                <p class="eyebrow">Provider</p>
                <h2>Choose the content network</h2>
                <p>
                  Each source keeps its own connection and capability state.
                </p>
              </div>
            </div>
            <div class="field">
              <label for="provider-id">Content provider</label>
              <select id="provider-id" v-model="provider" required>
                <option
                  v-for="item in store.items"
                  :key="item.provider_id"
                  :value="item.provider_id"
                >
                  {{ item.provider_id }} — {{ item.state }}
                </option>
              </select>
            </div>
          </div>
        </div>

        <div class="setup-step">
          <span class="step-number">2</span>
          <div class="step-content">
            <div class="surface-card-header">
              <div>
                <p class="eyebrow">Access</p>
                <h2>Select a connection method</h2>
                <p>
                  Use anonymous access when the provider supports public reads.
                </p>
              </div>
            </div>
            <div class="method-grid">
              <label :class="{ selected: method === 'builtin.anonymous' }">
                <input
                  v-model="method"
                  type="radio"
                  value="builtin.anonymous"
                />
                <span>
                  <strong>Anonymous</strong>
                  <small>No credential · public content only</small>
                </span>
              </label>
              <label :class="{ selected: method === 'builtin.manual' }">
                <input v-model="method" type="radio" value="builtin.manual" />
                <span>
                  <strong>Credential</strong>
                  <small>Cookie or token · stored in local vault</small>
                </span>
              </label>
            </div>
            <label class="visually-hidden" for="method-id"
              >Connection method</label
            >
            <select
              id="method-id"
              v-model="method"
              class="visually-hidden"
              required
            >
              <option value="builtin.anonymous">
                Anonymous (no credential)
              </option>
              <option value="builtin.manual">Credential / cookie</option>
            </select>
          </div>
        </div>

        <div v-if="method === 'builtin.manual'" class="setup-step">
          <span class="step-number">3</span>
          <div class="step-content credential-step">
            <div class="surface-card-header">
              <div>
                <p class="eyebrow">Credential</p>
                <h2>Secure provider access</h2>
                <p>
                  Values are write-only and never displayed after submission.
                </p>
              </div>
              <span class="badge">Local vault</span>
            </div>
            <div class="form-grid">
              <div class="field">
                <label for="field-id">Provider field ID</label>
                <input
                  id="field-id"
                  v-model="fieldId"
                  autocomplete="off"
                  required
                />
                <p class="field-hint">
                  Use the exact field requested by this provider.
                </p>
              </div>
              <div class="field">
                <label for="credential">Credential value</label>
                <input
                  id="credential"
                  v-model="credential"
                  type="password"
                  autocomplete="off"
                  placeholder="Paste credential"
                />
              </div>
            </div>
          </div>
        </div>

        <div class="connection-submit">
          <div>
            <strong>Ready to connect {{ provider }}</strong>
            <small>Requested permission: read public content</small>
          </div>
          <button type="submit" :disabled="store.connectPhase === 'loading'">
            {{
              store.connectPhase === "loading"
                ? "Connecting…"
                : "Connect source"
            }}
          </button>
        </div>
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
      <div class="surface-card-header">
        <div>
          <p class="eyebrow">Overview</p>
          <h2 id="source-status-heading">Source status</h2>
        </div>
      </div>
      <ul
        class="status-list"
        aria-label="Source connection statuses"
        aria-live="polite"
      >
        <li
          v-for="item in store.items"
          :key="`${item.provider_id}:${item.account_id ?? 'default'}`"
        >
          <div class="source-status">
            <strong>{{ item.provider_id }}</strong>
            <span>{{ item.state }}</span>
          </div>
          <span class="status-badge" :class="`status-${item.state}`">{{
            item.method_id || "Not configured"
          }}</span>
        </li>
      </ul>
    </section>
  </section>
</template>

<style scoped>
.back-link {
  color: var(--muted-foreground);
  font-size: 0.8rem;
  font-weight: 650;
  text-decoration: none;
}
.connection-form {
  display: grid;
  gap: 0;
  padding: 0;
  overflow: hidden;
}
.setup-step {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 1rem;
  border-bottom: 1px solid var(--border);
  padding: 1.25rem;
}
.step-number {
  display: grid;
  place-items: center;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 50%;
  background: var(--primary);
  color: var(--primary-foreground);
  font-size: 0.72rem;
  font-weight: 800;
}
.step-content {
  min-width: 0;
}
.method-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.65rem;
}
.method-grid > label {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 0.65rem;
  min-height: 5rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.85rem;
  background: var(--card);
  cursor: pointer;
}
.method-grid > label.selected {
  border-color: var(--brand);
  background: var(--brand-soft);
}
.method-grid input {
  margin-top: 0.18rem;
}
.method-grid span {
  display: grid;
  align-content: start;
  gap: 0.2rem;
}
.method-grid small {
  font-size: 0.72rem;
  font-weight: 450;
  line-height: 1.4;
}
.connection-submit {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem 1.25rem;
  background: var(--muted);
}
.connection-submit > div {
  display: grid;
  gap: 0.15rem;
}
.connection-submit strong {
  font-size: 0.82rem;
}
.connection-submit small {
  font-size: 0.69rem;
}
.connection-form > [role="status"],
.connection-form > [role="alert"] {
  margin: 0 1.25rem 1.25rem;
}
.status-list {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}
.status-list > li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border-top: 1px solid var(--border);
  padding: 0.65rem 0;
}
.status-list > li:first-child {
  border-top: 0;
}
.source-status {
  display: flex;
  gap: 0.5rem;
}
.source-status span {
  color: var(--muted-foreground);
  font-size: 0.75rem;
  text-transform: capitalize;
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
@media (max-width: 36rem) {
  .setup-step {
    grid-template-columns: minmax(0, 1fr);
  }
  .method-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .connection-submit {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
