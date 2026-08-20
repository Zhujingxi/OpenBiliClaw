<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { LoadPhase, UiError } from "../stores/state";
import LocalizedError from "./LocalizedError.vue";

defineProps<{ phase: LoadPhase; error?: UiError | undefined }>();
const { t } = useI18n();
</script>
<template>
  <p v-if="phase === 'loading'" role="status" aria-live="polite">
    {{ t("async.loading") }}
  </p>
  <div v-else-if="phase === 'empty'" role="status">
    <slot name="empty">{{ t("async.empty") }}</slot>
  </div>
  <p v-else-if="phase === 'error'" role="alert">
    <LocalizedError v-if="error" :error="error" />
    <template v-else>{{ t("async.failed") }}</template>
  </p>
  <slot v-else />
</template>
