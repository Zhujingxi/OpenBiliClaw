<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { computed, inject, onBeforeUnmount, onMounted, ref } from "vue";
import { useContentStore } from "../stores/content";
import { useSourcesStore } from "../stores/sources";
import type { ContentPreview } from "@openbiliclaw/presentation";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useContentStore();
const sources = useSourcesStore();
const provider = ref(store.lastProvider);
const query = ref(store.lastQuery);
// Providers without a declared search capability (e.g. v2ex) are not offered;
// entries without capability metadata stay visible for backward compatibility.
const searchable = computed(() =>
  sources.items.filter(
    (item) =>
      item.state === "connected" &&
      (item.capabilities === undefined ||
        item.capabilities.length === 0 ||
        item.capabilities.includes("search")),
  ),
);
onMounted(() => {
  void sources.load(api).then(() => {
    const first = searchable.value[0];
    if (
      first !== undefined &&
      !searchable.value.some((item) => item.provider_id === provider.value)
    ) {
      provider.value = first.provider_id;
    }
  });
});
function open(item: ContentPreview): void {
  // Providers without a fetch capability (e.g. weibo) have no in-app detail;
  // their previews are complete, so open the canonical URL instead.
  const capabilities = sources.items.find(
    (entry) => entry.provider_id === item.ref.provider_id.value,
  )?.capabilities;
  if (capabilities !== undefined && !capabilities.includes("fetch")) {
    window.open(item.ref.canonical_url, "_blank", "noopener");
    return;
  }
  location.hash = `#/content/${encodeURIComponent(JSON.stringify(item.ref))}`;
}
onBeforeUnmount(store.cancelSearch);
</script>
<template>
  <section>
    <h1 tabindex="-1">Search</h1>
    <p v-if="sources.phase === 'loading'" role="status" aria-live="polite">
      Loading connected sources…
    </p>
    <p v-else-if="sources.phase === 'error'" role="alert">
      {{ sources.error }}
    </p>
    <p v-else-if="searchable.length === 0" role="status">
      Search needs a connected source with search support.
      <a href="#/connect">Connect a source</a> to continue.
    </p>
    <form
      v-else
      role="search"
      @submit.prevent="store.search(api, provider, query)"
    >
      <label for="search-provider">Provider</label>
      <select id="search-provider" v-model="provider" required>
        <option
          v-for="item in searchable"
          :key="item.provider_id"
          :value="item.provider_id"
        >
          {{ item.provider_id }}
        </option>
      </select>
      <label for="search-query">Search content</label>
      <input id="search-query" v-model="query" required />
      <button type="submit">Search</button>
    </form>
    <AsyncState :phase="store.searchPhase" :error="store.searchError">
      <template #empty>No matching content was found.</template>
      <ul class="card-list">
        <li
          v-for="item in store.results.items"
          :key="item.ref.provider_content_id"
        >
          <button type="button" @click="open(item)">{{ item.title }}</button>
        </li>
      </ul>
    </AsyncState>
  </section>
</template>
<style scoped>
.card-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 20rem), 1fr));
  gap: 1rem;
}

.card-list > li {
  min-width: 0;
}
</style>
