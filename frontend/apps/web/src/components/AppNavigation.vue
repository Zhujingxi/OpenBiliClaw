<script setup lang="ts">
import type { RouteName } from "../app/routes";

defineProps<{ current: RouteName; mobile?: boolean }>();

const groups = [
  {
    label: "Discover",
    routes: [
      ["recommendations", "For you", "✦"],
      ["search", "Search", "⌕"],
    ],
  },
  {
    label: "Workspace",
    routes: [
      ["assistant", "Assistant", "◈"],
      ["providers", "Content sources", "▦"],
      ["profile", "Taste profile", "◎"],
    ],
  },
  {
    label: "System",
    routes: [
      ["settings", "Settings", "⚙"],
      ["runtime", "Runtime health", "●"],
    ],
  },
] as const;

const mobileRoutes = [
  ["recommendations", "Home", "✦"],
  ["search", "Search", "⌕"],
  ["assistant", "Assistant", "◈"],
  ["providers", "Sources", "▦"],
  ["settings", "Settings", "⚙"],
] as const;
</script>

<template>
  <nav
    :aria-label="mobile ? 'Mobile navigation' : 'Primary navigation'"
    :class="mobile ? 'mobile-nav' : 'desktop-nav'"
  >
    <template v-if="mobile">
      <a
        v-for="[name, label, icon] in mobileRoutes"
        :key="name"
        :href="`#/${name}`"
        :aria-current="current === name ? 'page' : undefined"
      >
        <span class="nav-icon" aria-hidden="true">{{ icon }}</span>
        <span>{{ label }}</span>
      </a>
    </template>
    <template v-else>
      <div v-for="group in groups" :key="group.label" class="nav-group">
        <p>{{ group.label }}</p>
        <a
          v-for="[name, label, icon] in group.routes"
          :key="name"
          :href="`#/${name}`"
          :aria-current="current === name ? 'page' : undefined"
        >
          <span class="nav-icon" aria-hidden="true">{{ icon }}</span>
          <span>{{ label }}</span>
        </a>
      </div>
    </template>
  </nav>
</template>
