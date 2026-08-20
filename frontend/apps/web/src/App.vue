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
  routeTranslationKey,
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
import { useI18n } from "vue-i18n";

const { t, locale } = useI18n();
const current = ref<RouteName>(routeFromHash(location.hash));
const unknownRoute = ref(!isKnownRouteHash(location.hash));
document.title = t("app.title", {
  page: t(routeTranslationKey(current.value)),
});
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
const updateTitle = (): void => {
  document.title = t("app.title", {
    page: t(routeTranslationKey(current.value)),
  });
};
watch(locale, updateTitle);
watch(current, async (route) => {
  updateTitle();
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
      {{ t("app.skip") }}
    </a>
    <div
      class="responsive-layout"
      :class="{ 'login-layout': current === 'login' }"
    >
      <aside v-if="current !== 'login'" class="sidebar">
        <a class="brand" href="#/recommendations" :aria-label="t('app.home')">
          <span class="brand-mark" aria-hidden="true">O</span>
          <span>
            <strong>OpenBiliClaw</strong>
            <small>{{ t("app.tagline") }}</small>
          </span>
        </a>
        <AppNavigation :current="current" />
        <p class="local-note">
          <span aria-hidden="true"></span> {{ t("app.local") }}
        </p>
      </aside>
      <div class="workspace">
        <header v-if="current !== 'login'" class="topbar">
          <div>
            <span class="topbar-kicker">{{ t("nav.workspace") }}</span>
            <strong>{{ t(routeTranslationKey(current)) }}</strong>
          </div>
          <a class="topbar-action" href="#/settings">{{ t("app.settings") }}</a>
        </header>
        <main id="main" tabindex="-1">
          <p v-if="unknownRoute" class="route-notice" role="status">
            {{ t("app.notFound") }}
          </p>
          <component :is="view" />
        </main>
        <AppNavigation v-if="current !== 'login'" :current="current" mobile />
      </div>
    </div>
  </div>
</template>
