<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount } from "vue";
import { useContentStore } from "../stores/content";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useContentStore();
onBeforeUnmount(store.cancel);
</script>
<template>
  <section>
    <h1 tabindex="-1">Content detail</h1>
    <button type="button" @click="store.fetchDetail(api, 'selected')">
      Load selected content</button
    ><AsyncState :phase="store.phase" :error="store.error">
      <pre v-if="store.detail" aria-label="Content details">{{
        store.detail.content.ref.provider_content_id
      }}</pre>
    </AsyncState>
  </section>
</template>
