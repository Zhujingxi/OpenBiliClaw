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
import {
  isKnownRouteHash,
  routeFromHash,
  routeLabel,
  type RouteName,
} from "./app/routes";
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
const unknownRoute = ref(!isKnownRouteHash(location.hash));
document.title = `${routeLabel(current.value)} · OpenBiliClaw`;
const update = (): void => {
  current.value = routeFromHash(location.hash);
  unknownRoute.value = !isKnownRouteHash(location.hash);
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
  document.title = `${routeLabel(route)} · OpenBiliClaw`;
  if (auth.status === "required" && route !== "login") {
    location.hash = "#/login";
    return;
  }
  if (auth.status !== "required" && route === "login") {
    location.hash = "#/recommendations";
    return;
  }
  await nextTick();
  document.documentElement.scrollTop = 0;
  document.body.scrollTop = 0;
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
    <a class="skip-link" href="#main" @click.prevent="focusMain">
      Skip to content
    </a>
    <div
      class="responsive-layout"
      :class="{ 'login-layout': current === 'login' }"
    >
      <aside v-if="current !== 'login'" class="sidebar">
        <a
          class="brand"
          href="#/recommendations"
          aria-label="OpenBiliClaw home"
        >
          <span class="brand-mark" aria-hidden="true">O</span>
          <span>
            <strong>OpenBiliClaw</strong>
            <small>Personal discovery</small>
          </span>
        </a>
        <AppNavigation :current="current" />
        <p class="local-note">
          <span aria-hidden="true"></span> Local-first workspace
        </p>
      </aside>
      <div class="workspace">
        <header v-if="current !== 'login'" class="topbar">
          <div>
            <span class="topbar-kicker">Workspace</span>
            <strong>{{ routeLabel(current) }}</strong>
          </div>
          <a class="topbar-action" href="#/settings">Settings</a>
        </header>
        <main id="main" tabindex="-1">
          <p v-if="unknownRoute" class="route-notice" role="status">
            Page not found; showing For you.
          </p>
          <component :is="view" />
        </main>
        <AppNavigation v-if="current !== 'login'" :current="current" mobile />
      </div>
    </div>
  </div>
</template>
