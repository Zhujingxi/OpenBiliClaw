<script setup lang="ts">
import { routes, type RouteName } from "../app/routes";
defineProps<{ current: RouteName; mobile?: boolean }>();
</script>
<template>
  <nav
    :aria-label="mobile ? 'Mobile navigation' : 'Primary navigation'"
    :class="mobile ? 'mobile-nav' : 'desktop-nav'"
  >
    <a
      v-for="[name, label] in routes"
      :key="name"
      :href="`#/${name}`"
      :aria-current="current === name ? 'page' : undefined"
      >{{ label }}</a
    >
  </nav>
</template>
