<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, onMounted } from "vue";
import { useProfileStore } from "../stores/profile";
import type { WebApi } from "../services/api";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useProfileStore();
onMounted(() => store.load(api));
onBeforeUnmount(store.cancel);
</script>
<template>
  <section>
    <h1 tabindex="-1">Profile</h1>
    <AsyncState :phase="store.phase" :error="store.error">
      <h2>Preferences</h2>
      <ul>
        <li
          v-for="item in store.result?.profile.preference_summary ?? []"
          :key="item"
        >
          {{ item }}
        </li>
      </ul>
      <h2>Insights</h2>
      <ul>
        <li v-for="item in store.result?.profile.insights ?? []" :key="item">
          {{ item }}
        </li>
      </ul>
    </AsyncState>
  </section>
</template>
