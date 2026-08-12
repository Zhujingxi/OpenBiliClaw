<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, onMounted } from "vue";
import { useSourcesStore } from "../stores/sources";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useSourcesStore();
onMounted(() => store.load(api));
onBeforeUnmount(store.cancel);
</script>
<template>
  <section>
    <h1 tabindex="-1">Providers</h1>
    <AsyncState :phase="store.phase" :error="store.error">
      <div class="tabs" role="tablist" aria-label="Connected providers">
        <button
          v-for="item in store.items"
          :key="item.provider_id"
          role="tab"
          :aria-selected="item.state === 'connected'"
        >
          {{ item.provider_id }} · {{ item.state }}
        </button>
      </div>
    </AsyncState>
  </section>
</template>
