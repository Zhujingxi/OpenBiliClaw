<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, onMounted } from "vue";
import { useProfileStore } from "../stores/profile";
import type { WebApi } from "../services/api";
import { useI18n } from "vue-i18n";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const { t } = useI18n();
const store = useProfileStore();
onMounted(() => store.load(api));
onBeforeUnmount(store.cancel);
</script>
<template>
  <section>
    <h1 tabindex="-1">{{ t("profile.title") }}</h1>
    <p>
      {{ t("profile.emptyLead") }}
      <a href="#/connect">{{ t("common.connectSource") }}</a>
    </p>
    <AsyncState :phase="store.phase" :error="store.error">
      <template #empty>{{ t("profile.empty") }}</template>
      <h2>{{ t("profile.preferences") }}</h2>
      <ul>
        <li
          v-for="item in store.result?.profile.preference_summary ?? []"
          :key="item"
        >
          {{ item }}
        </li>
      </ul>
      <h2>{{ t("profile.insights") }}</h2>
      <ul>
        <li v-for="item in store.result?.profile.insights ?? []" :key="item">
          {{ item }}
        </li>
      </ul>
    </AsyncState>
  </section>
</template>
