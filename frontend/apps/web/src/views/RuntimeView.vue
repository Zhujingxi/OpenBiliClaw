<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, onMounted } from "vue";
import { useRuntimeStore } from "../stores/runtime";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useRuntimeStore();
onMounted(() => {
  void store.load(api);
  void store.connect(api);
});
onBeforeUnmount(() => {
  store.cancel();
  store.disconnect();
});
</script>
<template>
  <section>
    <h1 tabindex="-1">Runtime health</h1>
    <p role="status" aria-live="polite">
      Events {{ store.streamConnected ? "connected" : "disconnected" }}
    </p>
    <AsyncState :phase="store.phase" :error="store.error">
      <dl v-if="store.health">
        <dt>Status</dt>
        <dd>{{ store.health.health.status }}</dd>
        <dt>Component</dt>
        <dd>{{ store.health.health.component_id }}</dd>
      </dl>
    </AsyncState>
  </section>
</template>
