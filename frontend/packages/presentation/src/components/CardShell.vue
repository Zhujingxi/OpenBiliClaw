<script setup lang="ts">
import { computed } from "vue";
import type { CardView } from "../contracts";
import { sanitizeUrl } from "../url";

const props = defineProps<{
  card: CardView;
  fallbackMessage?: string | undefined;
}>();
const titleId = `card-${Math.random().toString(36).slice(2)}-title`;
const href = computed(() => sanitizeUrl(props.card.data.ref.canonical_url));
const mediaUrl = computed(() =>
  sanitizeUrl(props.card.data.image_url ?? undefined),
);
const summary = computed(() => props.card.data.summary.slice(0, 500));
const status = computed(() => {
  if (props.card.availability === "deleted") return "Content unavailable";
  if (props.card.availability === "provider-unavailable")
    return "Provider unavailable";
  return undefined;
});
</script>

<template>
  <article :aria-labelledby="titleId" class="obc-card">
    <img v-if="mediaUrl" :src="mediaUrl" alt="" loading="lazy" />
    <p v-if="fallbackMessage" role="status">{{ fallbackMessage }}</p>
    <p v-if="status" role="status">{{ status }}</p>
    <h2 :id="titleId">
      <a v-if="href" :href="href" rel="noopener noreferrer">{{
        card.data.title
      }}</a>
      <span v-else>{{ card.data.title }}</span>
    </h2>
    <p data-card-summary>{{ summary }}</p>
    <p aria-label="Provider">{{ card.providerLabel }}</p>
    <div role="group" aria-label="Feedback actions">
      <button type="button" aria-label="Like recommendation">Like</button>
      <button type="button" aria-label="Dismiss recommendation">Dismiss</button>
    </div>
  </article>
</template>
