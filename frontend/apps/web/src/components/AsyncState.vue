<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { LoadPhase } from "../stores/state";
defineProps<{ phase: LoadPhase; error?: string | undefined }>();
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
    {{ error ?? t("async.failed") }}
  </p>
  <slot v-else />
</template>
