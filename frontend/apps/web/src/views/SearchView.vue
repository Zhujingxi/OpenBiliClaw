<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import LocalizedError from "../components/LocalizedError.vue";
import { computed, inject, onBeforeUnmount, onMounted, ref } from "vue";
import { useContentStore } from "../stores/content";
import { useSourcesStore } from "../stores/sources";
import type { ContentPreview } from "@openbiliclaw/presentation";
import type { WebApi } from "../services/api";
import { useI18n } from "vue-i18n";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const { t } = useI18n();
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
    <div class="page-heading">
      <div class="page-heading-copy">
        <p class="eyebrow">{{ t("search.eyebrow") }}</p>
        <h1 tabindex="-1">{{ t("search.title") }}</h1>
        <p>{{ t("search.intro") }}</p>
      </div>
    </div>
    <p v-if="sources.phase === 'loading'" role="status" aria-live="polite">
      {{ t("search.loadingSources") }}
    </p>
    <p v-else-if="sources.phase === 'error' && sources.error" role="alert">
      <LocalizedError :error="sources.error" />
    </p>
    <p v-else-if="searchable.length === 0" role="status">
      {{ t("search.noProvider") }}
      <a href="#/connect">{{ t("common.connectSource") }}</a>
      {{ t("search.continue") }}
    </p>
    <form
      v-else
      class="search-form"
      role="search"
      @submit.prevent="store.search(api, provider, query)"
    >
      <div class="field">
        <label for="search-provider">{{ t("search.provider") }}</label>
        <select id="search-provider" v-model="provider" required>
          <option
            v-for="item in searchable"
            :key="item.provider_id"
            :value="item.provider_id"
          >
            {{ item.provider_id }}
          </option>
        </select>
      </div>
      <div class="field search-query">
        <label for="search-query">{{ t("search.query") }}</label>
        <input
          id="search-query"
          v-model="query"
          required
          :placeholder="t('search.placeholder')"
          aria-describedby="search-query-help"
        />
        <p id="search-query-help" class="field-hint">
          {{ t("search.queryHelp") }}
        </p>
      </div>
      <button type="submit">{{ t("search.submit") }}</button>
    </form>
    <AsyncState :phase="store.searchPhase" :error="store.searchError">
      <template #empty>{{ t("search.empty") }}</template>
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
.search-form {
  display: grid;
  grid-template-columns: minmax(10rem, 0.55fr) minmax(14rem, 1.45fr) auto;
  gap: 0.75rem;
  align-items: end;
}
.search-form button {
  min-width: 6rem;
}
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
.card-list button {
  width: 100%;
  min-height: 5rem;
  border-color: var(--border);
  padding: 1rem;
  background: var(--card);
  color: var(--foreground);
  text-align: left;
  box-shadow: var(--shadow-sm);
}
.card-list button:hover {
  border-color: var(--brand);
  background: var(--brand-soft);
}
@media (max-width: 42rem) {
  .search-form {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
