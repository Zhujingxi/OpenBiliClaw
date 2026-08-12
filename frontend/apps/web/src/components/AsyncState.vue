<script setup lang="ts">
import type { LoadPhase } from "../stores/state";
defineProps<{ phase: LoadPhase; error?: string | undefined }>();
</script>
<template>
  <p v-if="phase === 'loading'" role="status" aria-live="polite">Loading…</p>
  <p v-else-if="phase === 'empty'" role="status">Nothing here yet.</p>
  <p v-else-if="phase === 'error'" role="alert">
    {{ error ?? "Request failed" }}
  </p>
  <slot v-else />
</template>
