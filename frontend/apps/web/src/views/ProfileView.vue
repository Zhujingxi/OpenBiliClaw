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
    <p>
      This read-only profile is derived automatically from content you view and
      recommendation feedback you provide.
      <a href="#/connect">Connect a source</a>
      to start activity; preferences and insights will appear here as the system
      learns.
    </p>
    <AsyncState :phase="store.phase" :error="store.error">
      <template #empty> No learned preferences or insights yet. </template>
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
