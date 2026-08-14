<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { routeParameter } from "../app/routes";
import { useContentStore } from "../stores/content";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useContentStore();
const reference = ref(routeParameter(location.hash));

// Payload shape is provider-owned; render only the optional fields that exist.
type Payload = Record<string, unknown>;
const payload = computed<Payload>(
  () => (store.detail?.content.payload ?? {}) as Payload,
);
function text(key: string): string | undefined {
  const value = payload.value[key];
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}
const title = computed(() => text("title"));
const description = computed(() => text("description") ?? text("summary"));
const image = computed(
  () => text("thumbnail_url") ?? text("cover_url") ?? text("image_url"),
);
const creator = computed(() => {
  for (const key of ["channel", "creator"]) {
    const value = payload.value[key];
    if (typeof value === "object" && value !== null && "name" in value) {
      const name = (value as Record<string, unknown>).name;
      if (typeof name === "string" && name.trim() !== "") return name;
    }
  }
  return undefined;
});
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
      <article v-if="store.detail">
        <h2 v-if="title">{{ title }}</h2>
        <img v-if="image" :src="image" alt="" class="detail-image" />
        <p v-if="creator">{{ creator }}</p>
        <p v-if="description" class="detail-description">{{ description }}</p>
        <p>
          <a
            :href="store.detail.content.ref.canonical_url"
            target="_blank"
            rel="noopener noreferrer"
            >Open on {{ store.detail.content.ref.provider_id.value }}</a
          >
        </p>
        <dl>
          <dt>Provider</dt>
          <dd>{{ store.detail.content.ref.provider_id.value }}</dd>
          <dt>Content ID</dt>
          <dd>{{ store.detail.content.ref.provider_content_id }}</dd>
          <dt>Kind</dt>
          <dd>{{ store.detail.content.ref.content_kind.value }}</dd>
        </dl>
      </article>
    </AsyncState>
  </section>
</template>
<style scoped>
.detail-image {
  max-width: 20rem;
  height: auto;
}
.detail-description {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 8;
  overflow: hidden;
}
</style>
