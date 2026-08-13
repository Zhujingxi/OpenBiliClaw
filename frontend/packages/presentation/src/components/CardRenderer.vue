<script setup lang="ts">
import { computed, type Component } from "vue";
import ArticleCard from "../cards/ArticleCard.vue";
import DiscussionCard from "../cards/DiscussionCard.vue";
import FallbackCard from "../cards/FallbackCard.vue";
import ImageCard from "../cards/ImageCard.vue";
import VideoCard from "../cards/VideoCard.vue";
import type { CardKind, CardView } from "../contracts";

const props = defineProps<{ card: CardView }>();
const emit = defineEmits<{
  like: [card: CardView];
  dismiss: [card: CardView];
}>();
const trustedRenderers: Readonly<Partial<Record<CardKind, Component>>> = {
  video: VideoCard,
  image: ImageCard,
  article: ArticleCard,
  discussion: DiscussionCard,
  fallback: FallbackCard,
};
const versionSupported = computed(() => props.card.version === 1);
const renderer = computed(() =>
  versionSupported.value
    ? (trustedRenderers[props.card.kind] ?? FallbackCard)
    : FallbackCard,
);
const fallbackMessage = computed(() =>
  versionSupported.value
    ? undefined
    : "Unsupported card version; showing fallback.",
);
</script>
<template>
  <component
    :is="renderer"
    :card="card"
    :fallback-message="fallbackMessage"
    :data-fallback="renderer === FallbackCard ? 'true' : undefined"
    @like="emit('like', card)"
    @dismiss="emit('dismiss', card)"
  />
</template>
