<script setup lang="ts">
import type { LoadPhase } from "../stores/state";
defineProps<{ phase: LoadPhase; error?: string | undefined }>();
</script>
<template>
  <p v-if="phase === 'loading'" role="status" aria-live="polite">Loading…</p>
  <div v-else-if="phase === 'empty'" role="status">
    <slot name="empty">Nothing here yet.</slot>
  </div>
  <p v-else-if="phase === 'error'" role="alert">
    {{ error ?? "Request failed" }}
  </p>
  <slot v-else />
</template>
