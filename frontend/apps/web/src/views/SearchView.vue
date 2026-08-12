<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, ref } from "vue";
import { useContentStore } from "../stores/content";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useContentStore();
const provider = ref("bilibili");
const query = ref("");
onBeforeUnmount(store.cancel);
</script>
<template>
  <section>
    <h1 tabindex="-1">Search</h1>
    <form role="search" @submit.prevent="store.search(api, provider, query)">
      <label for="search-provider">Provider</label>
      <input id="search-provider" v-model="provider" required />
      <label for="search-query">Search content</label>
      <input id="search-query" v-model="query" required /><button type="submit">
        Search
      </button>
    </form>
    <AsyncState :phase="store.phase" :error="store.error">
      <ul>
        <li
          v-for="item in store.results.items"
          :key="item.ref.provider_content_id"
        >
          {{ item.title }}
        </li>
      </ul>
    </AsyncState>
  </section>
</template>
