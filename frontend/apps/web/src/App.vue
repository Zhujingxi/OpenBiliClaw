<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import AppNavigation from "./components/AppNavigation.vue";
import { routeFromHash, type RouteName } from "./app/routes";
import RecommendationsView from "./views/RecommendationsView.vue";
import ProvidersView from "./views/ProvidersView.vue";
import SearchView from "./views/SearchView.vue";
import ContentView from "./views/ContentView.vue";
import ProfileView from "./views/ProfileView.vue";
import AssistantView from "./views/AssistantView.vue";
import ConnectView from "./views/ConnectView.vue";
import SettingsView from "./views/SettingsView.vue";
import RuntimeView from "./views/RuntimeView.vue";
import LoginView from "./views/LoginView.vue";
import { usePreferencesStore } from "./stores/preferences";
import { useAuthStore } from "./stores/auth";
const current = ref<RouteName>(routeFromHash(location.hash));
const update = (): void => {
  current.value = routeFromHash(location.hash);
};
onMounted(() => addEventListener("hashchange", update));
onBeforeUnmount(() => removeEventListener("hashchange", update));
const views = {
  recommendations: RecommendationsView,
  providers: ProvidersView,
  search: SearchView,
  content: ContentView,
  profile: ProfileView,
  assistant: AssistantView,
  connect: ConnectView,
  settings: SettingsView,
  runtime: RuntimeView,
  login: LoginView,
} as const;
const view = computed(() => views[current.value]);
const preferences = usePreferencesStore();
const auth = useAuthStore();
const goBack = (): void => history.back();
const focusMain = (): void =>
  document.querySelector<HTMLElement>("main")?.focus();
watch(current, async (route) => {
  if (auth.status === "required" && route !== "login") {
    location.hash = "#/login";
    return;
  }
  if (auth.status !== "required" && route === "login") {
    location.hash = "#/recommendations";
    return;
  }
  await nextTick();
  document.querySelector<HTMLElement>("main h1")?.focus();
});
</script>
<template>
  <div
    class="shell"
    :class="[
      `density-${preferences.density}`,
      { 'reduce-motion': preferences.reducedMotion },
    ]"
    @keydown.alt.left.prevent="goBack"
  >
    <a class="skip-link" href="#main" @click.prevent="focusMain"
      >Skip to content</a
    >
    <header><strong>OpenBiliClaw</strong></header>
    <div class="responsive-layout">
      <AppNavigation v-if="current !== 'login'" :current="current" />
      <main id="main" tabindex="-1"><component :is="view" /></main>
      <AppNavigation v-if="current !== 'login'" :current="current" mobile />
    </div>
  </div>
</template>
<style>
:root {
  color-scheme: light dark;
  font-family: system-ui, sans-serif;
  background: #111827;
  color: #f9fafb;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
}
#app,
.shell,
.responsive-layout,
main,
section,
form,
fieldset {
  min-width: 0;
  max-width: 100%;
}
p,
dd,
label {
  overflow-wrap: anywhere;
}
input:not([type="checkbox"]),
textarea,
select {
  width: 100%;
  min-width: 0;
}
a {
  color: #93c5fd;
}
button,
input,
textarea,
select {
  font: inherit;
  padding: 0.55rem;
}
button:focus-visible,
a:focus-visible,
input:focus-visible,
textarea:focus-visible,
select:focus-visible {
  outline: 3px solid #fbbf24;
  outline-offset: 2px;
}
.shell {
  min-height: 100vh;
}
.responsive-layout {
  display: grid;
  grid-template-columns: 14rem 1fr;
}
header {
  grid-column: 1/-1;
  padding: 1rem;
  border-bottom: 1px solid #374151;
}
nav {
  padding: 1rem;
  display: flex;
  gap: 0.5rem;
}
.desktop-nav {
  flex-direction: column;
}
.mobile-nav {
  display: none;
}
main {
  padding: clamp(1rem, 4vw, 3rem);
  max-width: 70rem;
  width: 100%;
}
section {
  display: grid;
  gap: 1rem;
}
form {
  display: grid;
  gap: 0.5rem;
  max-width: 36rem;
}
.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.skip-link {
  position: absolute;
  transform: translateY(-200%);
}
.skip-link:focus {
  transform: none;
  z-index: 2;
  background: #111827;
  padding: 0.75rem;
}
.density-compact main {
  padding: 1rem;
}
.reduce-motion * {
  scroll-behavior: auto !important;
  animation: none !important;
  transition: none !important;
}
@media (max-width: 48rem) {
  .responsive-layout {
    display: block;
    padding-bottom: 8rem;
  }
  .desktop-nav {
    display: none;
  }
  .mobile-nav {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    position: fixed;
    bottom: 0;
    inset-inline: 0;
    padding: 0.5rem;
    background: #111827;
    border-top: 1px solid #4b5563;
    z-index: 1;
  }
  .mobile-nav a {
    min-width: 0;
    padding: 0.25rem;
    text-align: center;
    font-size: 0.75rem;
    overflow-wrap: anywhere;
  }
}
@media (prefers-reduced-motion: reduce) {
  * {
    scroll-behavior: auto !important;
    animation: none !important;
    transition: none !important;
  }
}
</style>
