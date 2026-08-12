<script setup lang="ts">
import { usePreferencesStore } from "../stores/preferences";
const store = usePreferencesStore();
</script>
<template>
  <section>
    <h1 tabindex="-1">Settings</h1>
    <label for="density">Display density</label
    ><select id="density" v-model="store.density">
      <option value="comfortable">Comfortable</option>
      <option value="compact">Compact</option></select
    ><label
      ><input v-model="store.reducedMotion" type="checkbox" /> Reduce
      motion</label
    >
  </section>
</template>
