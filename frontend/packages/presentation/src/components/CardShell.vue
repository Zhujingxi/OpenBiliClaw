<script setup lang="ts">
import { computed, inject, type ComputedRef } from "vue";
import {
  defaultCardLabels,
  type CardLabels,
  type CardView,
} from "../contracts";
import { proxyImageUrl } from "../url";

const props = defineProps<{
  card: CardView;
  fallbackMessage?: string | undefined;
}>();
const labels = inject<ComputedRef<CardLabels>>(
  "card-labels",
  computed(() => defaultCardLabels),
);
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
const providerInitial = computed(
  () => props.card.providerLabel.trim().charAt(0).toUpperCase() || "•",
);
const summary = computed(() => {
  const value = props.card.data.summary.trim();
  return value && !/^[-–—]+$/.test(value) ? value.slice(0, 500) : undefined;
});
const status = computed(() => {
  if (props.card.availability === "deleted") return labels.value.unavailable;
  if (props.card.availability === "provider-unavailable")
    return labels.value.providerUnavailable;
  return undefined;
});
</script>

<template>
  <article :aria-labelledby="titleId" class="obc-card">
    <div
      class="obc-card__media"
      :class="{ 'obc-card__media--empty': !mediaUrl }"
    >
      <img v-if="mediaUrl" :src="mediaUrl" alt="" loading="lazy" />
      <span v-else aria-hidden="true">{{ providerInitial }}</span>
    </div>
    <div class="obc-card__content">
      <p v-if="fallbackMessage" role="status">{{ fallbackMessage }}</p>
      <p v-if="status" role="status">{{ status }}</p>
      <h2 :id="titleId">
        <a v-if="href" :href="href" rel="noopener noreferrer">{{
          card.data.title
        }}</a>
        <span v-else>{{ card.data.title }}</span>
      </h2>
      <p
        data-card-summary
        :aria-hidden="summary === undefined ? 'true' : undefined"
      >
        {{ summary ?? "\u00a0" }}
      </p>
      <div class="obc-card__meta">
        <p :aria-label="labels.provider">{{ card.providerLabel }}</p>
        <time
          :datetime="sourceTimestamp"
          :aria-hidden="sourceTimestamp === undefined ? 'true' : undefined"
        >
          {{
            sourceTimestamp
              ? new Date(sourceTimestamp).toLocaleString(labels.locale)
              : "\u00a0"
          }}
        </time>
      </div>
      <div role="group" :aria-label="labels.feedbackActions">
        <button
          type="button"
          class="card-like"
          :aria-label="labels.likeAria"
          @click="emit('like', card)"
        >
          {{ labels.like }}
        </button>
        <button
          type="button"
          class="card-dismiss"
          :aria-label="labels.dismissAria"
          @click="emit('dismiss', card)"
        >
          {{ labels.dismiss }}
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.obc-card {
  height: 100%;
  min-width: 0;
  background: var(--surface-strong);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: var(--shadow-sm);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease;
}

.obc-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-lg);
}

.obc-card__media {
  width: 100%;
  aspect-ratio: 16 / 9;
  flex: none;
  overflow: hidden;
  display: grid;
  place-items: center;
  background: var(--surface-soft);
}

.obc-card__media img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.obc-card__media--empty span {
  color: var(--text-muted);
  font-size: clamp(2rem, 6vw, 4rem);
  font-weight: 700;
  opacity: 0.35;
}

.obc-card__content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
}

.obc-card__content > p[role="status"] {
  margin: 0;
  background: var(--brand-soft);
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--brand-strong);
}

.obc-card__content > h2 {
  min-height: calc(2 * 1.38em);
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.38;
  color: var(--text-main);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.obc-card__content > h2 a {
  color: inherit;
}

.obc-card__content > p[data-card-summary] {
  min-height: calc(3 * 1.6em);
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.obc-card__meta {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
}

.obc-card__meta > p {
  margin: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--brand-soft);
  color: var(--brand);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.obc-card__meta > time {
  min-height: 1.5em;
  font-size: 12px;
  color: var(--text-muted);
}

.obc-card__content > div[role="group"] {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.obc-card__content > div[role="group"] button {
  padding: 6px 14px;
  border-radius: 999px;
  font-size: 12.5px;
  font-weight: 600;
  box-shadow: none;
}

.obc-card__content > div[role="group"] button.card-like {
  background: var(--brand-soft);
  color: var(--brand);
}

.obc-card__content > div[role="group"] button.card-dismiss {
  background: var(--surface);
  border: 1px solid var(--line);
  color: var(--text-secondary);
}

.obc-card__content > div[role="group"] button:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

@media (max-width: 640px) {
  .obc-card__content > h2,
  .obc-card__content > p[data-card-summary] {
    min-height: 0;
  }

  .obc-card__content > p[data-card-summary][aria-hidden="true"],
  .obc-card__meta > time[aria-hidden="true"] {
    display: none;
  }
}
</style>
