<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { computed, inject, onBeforeUnmount, onMounted } from "vue";
import { useSourcesStore } from "../stores/sources";
import type { WebApi } from "../services/api";

const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useSourcesStore();
const connectedCount = computed(
  () => store.items.filter((item) => item.state === "connected").length,
);
const hasConnectedSource = computed(() => connectedCount.value > 0);
function providerInitial(id: string): string {
  return id.slice(0, 1).toUpperCase();
}
onMounted(() => store.load(api));
onBeforeUnmount(store.cancel);
</script>

<template>
  <section class="sources-page">
    <div class="page-heading">
      <div class="page-heading-copy">
        <p class="eyebrow">Content layer</p>
        <h1 tabindex="-1">Content sources</h1>
        <p>
          Connect each provider once, then use its supported discovery features.
        </p>
      </div>
      <a class="connect-action" href="#/connect">Add or update a source</a>
    </div>

    <div class="source-overview surface-card">
      <div>
        <strong>{{ connectedCount }}</strong>
        <span>Connected</span>
      </div>
      <div>
        <strong>{{ store.items.length }}</strong>
        <span>Available</span>
      </div>
      <p>
        Credentials stay in the local vault. Source cards only show connection
        state and supported capabilities.
      </p>
    </div>

    <AsyncState :phase="store.phase" :error="store.error">
      <template #empty>
        No providers are available in this server configuration.
        <a href="#/connect">Review source connection options</a>.
      </template>
      <p v-if="!hasConnectedSource" class="empty-guidance" role="status">
        No sources are connected. <a href="#/connect">Connect a source</a> to
        search and personalize recommendations.
      </p>
      <ul class="provider-grid" aria-label="Provider connection statuses">
        <li
          v-for="item in store.items"
          :key="`${item.provider_id}:${item.account_id ?? 'default'}`"
          class="provider-card"
        >
          <div class="provider-card-head">
            <span class="provider-logo" aria-hidden="true">
              {{ providerInitial(item.provider_id) }}
            </span>
            <div>
              <div class="provider-status">
                <strong>{{ item.provider_id }}</strong>
                <span>{{ item.state }}</span>
              </div>
              <small>{{ item.account_id || "Default account" }}</small>
            </div>
            <span
              class="status-dot"
              :class="`status-${item.state}`"
              aria-hidden="true"
            ></span>
          </div>

          <div class="capability-list" aria-label="Supported capabilities">
            <span
              v-for="capability in item.capabilities ?? []"
              :key="capability"
            >
              {{ capability }}
            </span>
            <span v-if="!item.capabilities?.length"
              >Capabilities unavailable</span
            >
          </div>

          <div class="provider-card-footer">
            <span>{{ item.method_id || "No access method" }}</span>
            <a :href="`#/connect/${encodeURIComponent(item.provider_id)}`">
              {{ item.state === "connected" ? "Manage" : "Configure" }}
            </a>
          </div>
        </li>
      </ul>
    </AsyncState>
  </section>
</template>

<style scoped>
.connect-action {
  display: inline-flex;
  align-items: center;
  min-height: 2.5rem;
  border-radius: var(--radius-sm);
  padding: 0.55rem 0.9rem;
  background: var(--primary);
  color: var(--primary-foreground);
  font-size: 0.8rem;
  font-weight: 650;
  text-decoration: none;
}
.source-overview {
  display: grid;
  grid-template-columns: auto auto minmax(12rem, 1fr);
  gap: 1.5rem;
  align-items: center;
}
.source-overview > div {
  display: grid;
  min-width: 5rem;
}
.source-overview strong {
  font-size: 1.55rem;
  line-height: 1;
}
.source-overview span,
.source-overview p {
  color: var(--muted-foreground);
  font-size: 0.75rem;
}
.source-overview p {
  margin: 0;
  border-left: 1px solid var(--border);
  padding-left: 1.5rem;
}
.empty-guidance {
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  padding: 1rem;
}
.provider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 18rem), 1fr));
  gap: 0.8rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.provider-card {
  display: grid;
  gap: 1rem;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 1rem;
  background: var(--card);
  box-shadow: var(--shadow-sm);
  transition:
    border-color 120ms ease,
    transform 120ms ease;
}
.provider-card:hover {
  transform: translateY(-1px);
  border-color: #c7c3b8;
}
.provider-card-head {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 0.75rem;
  align-items: center;
}
.provider-logo {
  display: grid;
  place-items: center;
  width: 2.35rem;
  height: 2.35rem;
  border-radius: 0.7rem;
  background: var(--primary);
  color: var(--primary-foreground);
  font-size: 0.8rem;
  font-weight: 800;
}
.provider-card-head > div {
  min-width: 0;
}
.provider-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.provider-status strong,
.provider-status span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.provider-status span {
  color: var(--muted-foreground);
  font-size: 0.69rem;
  text-transform: capitalize;
}
.provider-card-head small {
  display: block;
  margin-top: 0.15rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.68rem;
}
.status-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: #aaa59a;
}
.status-dot.status-connected {
  background: var(--success);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--success) 12%, transparent);
}
.status-dot.status-disconnected {
  background: var(--warning);
}
.status-dot.status-error {
  background: var(--error);
}
.capability-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  min-height: 1.6rem;
}
.capability-list span {
  border-radius: 999px;
  padding: 0.25rem 0.5rem;
  background: var(--muted);
  color: var(--muted-foreground);
  font-size: 0.65rem;
  font-weight: 650;
}
.provider-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border-top: 1px solid var(--border);
  padding-top: 0.75rem;
  font-size: 0.72rem;
}
.provider-card-footer span {
  min-width: 0;
  overflow: hidden;
  color: var(--muted-foreground);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.provider-card-footer a {
  font-weight: 700;
  text-decoration: none;
}
@media (max-width: 36rem) {
  .source-overview {
    grid-template-columns: 1fr 1fr;
  }
  .source-overview p {
    grid-column: 1 / -1;
    border-top: 1px solid var(--border);
    border-left: 0;
    padding-top: 1rem;
    padding-left: 0;
  }
}
</style>
