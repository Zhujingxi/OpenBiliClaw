<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { RouteName } from "../app/routes";

defineProps<{ current: RouteName; mobile?: boolean }>();
const { t } = useI18n();

const groups = [
  {
    label: "nav.discover",
    routes: [
      ["recommendations", "nav.recommendations", "✦"],
      ["search", "nav.search", "⌕"],
    ],
  },
  {
    label: "nav.workspace",
    routes: [
      ["assistant", "nav.assistant", "◈"],
      ["providers", "nav.providers", "▦"],
      ["profile", "nav.profile", "◎"],
    ],
  },
  {
    label: "nav.system",
    routes: [
      ["settings", "nav.settings", "⚙"],
      ["runtime", "nav.runtime", "●"],
    ],
  },
] as const;
const mobileRoutes = [
  ["recommendations", "nav.home", "✦"],
  ["search", "nav.search", "⌕"],
  ["assistant", "nav.assistant", "◈"],
  ["providers", "nav.sources", "▦"],
  ["settings", "nav.settings", "⚙"],
] as const;
</script>

<template>
  <nav
    :aria-label="t(mobile ? 'nav.mobile' : 'nav.primary')"
    :class="mobile ? 'mobile-nav' : 'desktop-nav'"
  >
    <template v-if="mobile">
      <a
        v-for="[name, label, icon] in mobileRoutes"
        :key="name"
        :href="`#/${name}`"
        :aria-current="current === name ? 'page' : undefined"
      >
        <span class="nav-icon" aria-hidden="true">{{ icon }}</span
        ><span>{{ t(label) }}</span>
      </a>
    </template>
    <template v-else>
      <div v-for="group in groups" :key="group.label" class="nav-group">
        <p>{{ t(group.label) }}</p>
        <a
          v-for="[name, label, icon] in group.routes"
          :key="name"
          :href="`#/${name}`"
          :aria-current="current === name ? 'page' : undefined"
        >
          <span class="nav-icon" aria-hidden="true">{{ icon }}</span
          ><span>{{ t(label) }}</span>
        </a>
      </div>
    </template>
  </nav>
</template>
