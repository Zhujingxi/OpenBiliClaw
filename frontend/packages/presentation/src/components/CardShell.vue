<script setup lang="ts">
import { computed } from "vue";
import type { CardView } from "../contracts";
import { proxyImageUrl } from "../url";

const props = defineProps<{
  card: CardView;
  fallbackMessage?: string | undefined;
}>();
const emit = defineEmits<{
  like: [card: CardView];
  dismiss: [card: CardView];
}>();
const titleId = `card-${Math.random().toString(36).slice(2)}-title`;
const href = computed(
  () => `#/content/${encodeURIComponent(JSON.stringify(props.card.data.ref))}`,
);
const sourceTimestamp = computed(() => {
  const value = props.card.data.source_timestamp;
  return value && !value.startsWith("1970-01-01") ? value : undefined;
});
const mediaUrl = computed(() =>
  proxyImageUrl(props.card.data.image_url ?? undefined),
);
const summary = computed(() => {
  const value = props.card.data.summary.trim();
  return value && !/^[-–—]+$/.test(value) ? value.slice(0, 500) : undefined;
});
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
    <p v-if="summary" data-card-summary>{{ summary }}</p>
    <p aria-label="Provider">{{ card.providerLabel }}</p>
    <time v-if="sourceTimestamp" :datetime="sourceTimestamp">
      {{ new Date(sourceTimestamp).toLocaleString() }}
    </time>
    <div role="group" aria-label="Feedback actions">
      <button
        type="button"
        class="card-like"
        aria-label="Like recommendation"
        @click="emit('like', card)"
      >
        Like
      </button>
      <button
        type="button"
        class="card-dismiss"
        aria-label="Dismiss recommendation"
        @click="emit('dismiss', card)"
      >
        Dismiss
      </button>
    </div>
  </article>
</template>

<style scoped>
.obc-card {
  background: var(--surface-strong);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: var(--shadow-sm);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-bottom: 14px;
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease;
}

.obc-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-lg);
}

.obc-card > img {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  display: block;
  background: var(--surface-soft);
}

.obc-card > :not(img) {
  margin-inline: 14px;
}

.obc-card > p[role="status"] {
  background: var(--brand-soft);
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--brand-strong);
}

.obc-card > h2 {
  font-size: 15px;
  font-weight: 700;
  line-height: 1.38;
  color: var(--text-main);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.obc-card > h2 a {
  color: inherit;
}

.obc-card > p[data-card-summary] {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.obc-card > p[aria-label="Provider"] {
  align-self: flex-start;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--brand-soft);
  color: var(--brand);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.obc-card > time {
  font-size: 12px;
  color: var(--text-muted);
}

.obc-card > div[role="group"] {
  display: flex;
  gap: 8px;
  margin-top: auto;
}

.obc-card > div[role="group"] button {
  padding: 6px 14px;
  border-radius: 999px;
  font-size: 12.5px;
  font-weight: 600;
  box-shadow: none;
}

.obc-card > div[role="group"] button.card-like {
  background: var(--brand-soft);
  color: var(--brand);
}

.obc-card > div[role="group"] button.card-dismiss {
  background: var(--surface);
  border: 1px solid var(--line);
  color: var(--text-secondary);
}

.obc-card > div[role="group"] button:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}
</style>
