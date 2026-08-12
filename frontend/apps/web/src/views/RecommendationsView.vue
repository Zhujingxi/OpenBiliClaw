<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, onMounted } from "vue";
import { useRecommendationsStore } from "../stores/recommendations";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useRecommendationsStore();
onMounted(() => store.load(api));
onBeforeUnmount(store.cancel);
</script>
<template>
  <section>
    <h1 tabindex="-1">Recommendations</h1>
    <AsyncState :phase="store.phase" :error="store.error">
      <ol>
        <li v-for="item in store.page.items" :key="item.recommendation_id">
          Recommendation {{ item.rank }} · score {{ item.score }}
        </li>
      </ol>
    </AsyncState>
  </section>
</template>
