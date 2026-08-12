<script setup lang="ts">
import type { CardView } from "../contracts";
import CardShell from "../components/CardShell.vue";
defineProps<{ card: CardView; fallbackMessage?: string | undefined }>();
</script>
<template>
  <CardShell :card="card" :fallback-message="fallbackMessage" />
</template>
