<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { computed, inject, onBeforeUnmount, onMounted } from "vue";
import { useSourcesStore } from "../stores/sources";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useSourcesStore();
const hasConnectedSource = computed(() =>
  store.items.some((item) => item.state === "connected"),
);
onMounted(() => store.load(api));
onBeforeUnmount(store.cancel);
</script>
<template>
  <section>
    <h1 tabindex="-1">Providers</h1>
    <AsyncState :phase="store.phase" :error="store.error">
      <template #empty>
        No providers are available in this server configuration.
        <a href="#/connect">Review source connection options</a>.
      </template>
      <p v-if="!hasConnectedSource" role="status">
        No sources are connected. <a href="#/connect">Connect a source</a> to
        search and personalize recommendations.
      </p>
      <ul aria-label="Provider connection statuses">
        <li
          v-for="item in store.items"
          :key="item.provider_id"
          class="provider-status"
        >
          <strong>{{ item.provider_id }}</strong>
          <span>{{ item.state }}</span>
        </li>
      </ul>
    </AsyncState>
  </section>
</template>
<style scoped>
.provider-status {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
</style>
