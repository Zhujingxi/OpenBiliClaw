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
      <main id="main" tabindex="-1">
        <p v-if="unknownRoute" class="route-notice" role="status">
          Page not found; showing Recommendations.
        </p>
        <component :is="view" />
      </main>
      <AppNavigation v-if="current !== 'login'" :current="current" mobile />
    </div>
  </div>
</template>
<style>
:root {
  color-scheme: light;
  --bg-base: #fffafc;
  --bg-sky: #f2f8ff;
  --surface: rgba(255, 255, 255, 0.84);
  --surface-strong: rgba(255, 255, 255, 0.96);
  --surface-soft: rgba(255, 255, 255, 0.72);
  --line: rgba(217, 227, 242, 0.95);
  --line-strong: rgba(247, 173, 202, 0.46);
  --text-main: #20304a;
  --text-secondary: #60708c;
  --text-muted: #8f9bb0;
  --brand: #fb7299;
  --brand-strong: #f65788;
  --brand-soft: rgba(251, 114, 153, 0.12);
  --sky: #5aa9ff;
  --sky-soft: rgba(90, 169, 255, 0.14);
  --success: #30b980;
  --danger: #ef7a86;
  --shadow-lg: 0 20px 40px rgba(56, 76, 112, 0.12);
  --shadow-sm: 0 10px 22px rgba(73, 93, 130, 0.08);
  --focus-ring: 0 0 0 3px rgba(90, 169, 255, 0.28);
  --safe-bottom: env(safe-area-inset-bottom, 0px);
  font-family:
    -apple-system, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
    sans-serif;
  background: var(--bg-base);
  color: var(--text-main);
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  min-height: 100vh;
  background:
    radial-gradient(
      circle at 15% 12%,
      rgba(251, 114, 153, 0.2),
      transparent 30%
    ),
    radial-gradient(
      circle at 88% 18%,
      rgba(90, 169, 255, 0.24),
      transparent 26%
    ),
    linear-gradient(180deg, var(--bg-base) 0%, var(--bg-sky) 100%);
  background-attachment: fixed;
  -webkit-tap-highlight-color: transparent;
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
  color: var(--sky);
}
button,
input,
textarea,
select {
  font: inherit;
  padding: 0.55rem;
}
button {
  border: none;
  border-radius: 0.75rem;
  background: var(--brand);
  color: #fff;
  font-weight: 600;
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  transition: opacity 0.15s;
}
button:active {
  opacity: 0.7;
}
button:disabled {
  opacity: 0.4;
  cursor: default;
}
input:not([type="checkbox"]),
textarea,
select {
  background: var(--surface-strong);
  border: 1px solid var(--line);
  border-radius: 0.625rem;
  color: var(--text-main);
}
button:focus-visible,
a:focus-visible,
input:focus-visible,
textarea:focus-visible,
select:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
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
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--line);
  background: var(--surface-strong);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}
header strong {
  color: var(--brand);
  font-size: 1.05rem;
  letter-spacing: -0.01em;
}
nav {
  padding: 1rem;
  display: flex;
  gap: 0.5rem;
}
.desktop-nav {
  flex-direction: column;
}
.desktop-nav a {
  color: var(--text-secondary);
  text-decoration: none;
  border-radius: 0.75rem;
  padding: 0.4rem 0.55rem;
}
.desktop-nav a:hover {
  background: var(--brand-soft);
}
nav a[aria-current="page"] {
  color: var(--brand-strong);
  background: var(--brand-soft);
  border-radius: 0.75rem;
  font-weight: 700;
  text-decoration: none;
  padding: 0.4rem 0.55rem;
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
  top: 0.5rem;
  right: 0.5rem;
  transform: translateY(-200%);
}
.skip-link:focus {
  transform: none;
  z-index: 2;
  background: var(--surface-strong);
  color: var(--brand-strong);
  border-radius: 0.625rem;
  padding: 0.75rem;
}
.route-notice {
  padding: 0.75rem;
  border-inline-start: 0.25rem solid var(--brand);
  border-radius: 0.625rem;
  background: var(--brand-soft);
  color: var(--text-secondary);
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
    padding-bottom: calc(8rem + var(--safe-bottom));
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
    padding-bottom: calc(0.5rem + var(--safe-bottom));
    background: var(--surface-strong);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border-top: 1px solid var(--line);
    z-index: 1;
  }
  .mobile-nav a {
    min-width: 0;
    padding: 0.25rem;
    text-align: center;
    font-size: 0.75rem;
    color: var(--text-secondary);
    text-decoration: none;
    border-radius: 0.625rem;
    overflow-wrap: normal;
    word-break: normal;
    hyphens: none;
  }
  .mobile-nav a[aria-current="page"] {
    padding-inline: 0.125rem;
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
