<script setup lang="ts">
import { CardRenderer } from "@openbiliclaw/presentation";
import AsyncState from "../components/AsyncState.vue";
import LocalizedError from "../components/LocalizedError.vue";
import { inject, onBeforeUnmount, onMounted, type Directive } from "vue";
import { useRecommendationsStore } from "../stores/recommendations";
import type { WebApi } from "../services/api";
import { useI18n } from "vue-i18n";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useRecommendationsStore();
const { t, te, locale } = useI18n();
const stateLabel = (value: string | undefined): string => {
  if (!value) return "";
  const key = `common.states.${value}`;
  return te(key) ? t(key) : value;
};
const cardLabels = () => ({
  unavailable: t("cards.unavailable"),
  providerUnavailable: t("cards.providerUnavailable"),
  provider: t("cards.provider"),
  feedbackActions: t("cards.feedbackActions"),
  like: t("cards.like"),
  likeAria: t("cards.likeAria"),
  dismiss: t("cards.dismiss"),
  dismissAria: t("cards.dismissAria"),
  unsupported: t("cards.unsupported"),
  locale: locale.value,
});
const observers = new WeakMap<Element, IntersectionObserver>();
const vExposed: Directive<
  HTMLElement,
  import("@openbiliclaw/presentation").CardView
> = {
  mounted(element, { value: card }) {
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      store.markExposed(card);
      observer.disconnect();
      observers.delete(element);
    });
    observers.set(element, observer);
    observer.observe(element);
  },
  unmounted(element) {
    observers.get(element)?.disconnect();
    observers.delete(element);
  },
};
onMounted(() => store.load(api));
onBeforeUnmount(store.cancel);
</script>
<template>
  <section>
    <div class="page-heading">
      <div class="page-heading-copy">
        <p class="eyebrow">{{ t("recommendations.eyebrow") }}</p>
        <h1 tabindex="-1">{{ t("recommendations.title") }}</h1>
        <p>{{ t("recommendations.intro") }}</p>
      </div>
      <button
        type="button"
        class="secondary-action"
        :disabled="store.phase === 'loading'"
        @click="store.refresh(api)"
      >
        {{
          store.phase === "loading"
            ? t("recommendations.refreshing")
            : t("recommendations.refresh")
        }}
      </button>
    </div>
    <AsyncState :phase="store.phase" :error="store.error">
      <template #empty>
        {{ t("recommendations.empty") }}
        <a href="#/connect">{{ t("common.connectSource") }}</a>
        {{ t("recommendations.emptyHelp") }}
      </template>
      <ol class="card-list">
        <li v-for="card in store.cards" :key="card.shownId" v-exposed="card">
          <CardRenderer
            :card="card"
            :labels="cardLabels()"
            @like="store.like(api, $event)"
            @dismiss="store.dismiss(api, $event)"
          />
          <p
            v-if="card.shownId && store.feedbackState[card.shownId]"
            role="status"
          >
            {{
              t("recommendations.feedback", {
                state: stateLabel(store.feedbackState[card.shownId]),
              })
            }}
          </p>
          <p
            v-if="card.shownId && store.feedbackError[card.shownId]"
            role="alert"
          >
            <LocalizedError :error="store.feedbackError[card.shownId]!" />
          </p>
        </li>
      </ol>
    </AsyncState>
  </section>
</template>
<style scoped>
.secondary-action {
  width: auto;
}

.card-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 20rem), 1fr));
  gap: 1rem;
  align-items: stretch;
}

.card-list > li {
  min-width: 0;
  height: 100%;
}
</style>
