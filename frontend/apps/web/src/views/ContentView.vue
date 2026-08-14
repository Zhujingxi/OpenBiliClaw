<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { routeParameter } from "../app/routes";
import { useContentStore } from "../stores/content";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useContentStore();
const reference = ref(routeParameter(location.hash));
const updateReference = (): void => {
  reference.value = routeParameter(location.hash);
};
watch(
  reference,
  async (next) => {
    if (next !== undefined) await store.fetchDetail(api, next);
  },
  { immediate: true },
);
onMounted(() => addEventListener("hashchange", updateReference));
onBeforeUnmount(() => {
  removeEventListener("hashchange", updateReference);
  store.cancelDetail();
});
</script>
<template>
  <section>
    <h1 tabindex="-1">Content detail</h1>
    <p v-if="reference === undefined" role="status">
      Choose a result from Search.
    </p>
    <AsyncState v-else :phase="store.detailPhase" :error="store.detailError">
      <dl v-if="store.detail">
        <dt>Provider</dt>
        <dd>{{ store.detail.content.ref.provider_id.value }}</dd>
        <dt>Content ID</dt>
        <dd>{{ store.detail.content.ref.provider_content_id }}</dd>
        <dt>Kind</dt>
        <dd>{{ store.detail.content.ref.content_kind.value }}</dd>
      </dl>
    </AsyncState>
  </section>
</template>
