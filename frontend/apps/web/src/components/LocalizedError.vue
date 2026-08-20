<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { UiError } from "../stores/state";

defineProps<{ error: UiError }>();
const { t } = useI18n();
</script>

<template>{{ t(error.key, { status: error.status ?? "" }) }}</template>
