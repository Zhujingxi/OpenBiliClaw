<script setup lang="ts">
import { CardRenderer } from "@openbiliclaw/presentation";
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, onMounted, type Directive } from "vue";
import { useRecommendationsStore } from "../stores/recommendations";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useRecommendationsStore();
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
    <h1 tabindex="-1">Recommendations</h1>
    <button
      type="button"
      :disabled="store.phase === 'loading'"
      @click="store.load(api)"
    >
      Refresh feed
    </button>
    <AsyncState :phase="store.phase" :error="store.error">
      <ol class="card-list">
        <li v-for="card in store.cards" :key="card.shownId" v-exposed="card">
          <CardRenderer
            :card="card"
            @like="store.like(api, $event)"
            @dismiss="store.dismiss(api, $event)"
          />
          <p
            v-if="card.shownId && store.feedbackState[card.shownId]"
            role="status"
          >
            Feedback recorded: {{ store.feedbackState[card.shownId] }}
          </p>
          <p
            v-if="card.shownId && store.feedbackError[card.shownId]"
            role="alert"
          >
            {{ store.feedbackError[card.shownId] }}
          </p>
        </li>
      </ol>
    </AsyncState>
  </section>
</template>
