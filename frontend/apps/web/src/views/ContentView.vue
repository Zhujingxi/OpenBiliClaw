<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, onMounted, ref } from "vue";
import { routeParameter } from "../app/routes";
import { useContentStore } from "../stores/content";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useContentStore();
const reference = ref(routeParameter(location.hash));
onMounted(async () => {
  if (reference.value !== undefined)
    await store.fetchDetail(api, reference.value);
});
onBeforeUnmount(store.cancel);
</script>
<template>
  <section>
    <h1 tabindex="-1">Content detail</h1>
    <p v-if="reference === undefined" role="status">
      Choose a result from Search.
    </p>
    <AsyncState v-else :phase="store.phase" :error="store.error">
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
