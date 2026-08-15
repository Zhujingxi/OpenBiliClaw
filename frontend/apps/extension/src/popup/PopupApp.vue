<script setup lang="ts">
import { storeToRefs } from "pinia";
import { ref } from "vue";
import {
  chromeArtifacts,
  connectFromRecipe,
  discoverRecipes,
  openWarmup,
  type ProviderRecipe,
} from "./access-flow";
import { useConnectionStore } from "./connection-store";

const store = useConnectionStore();
store.hydrate();
const { backendUrl, deviceToken, state, error } = storeToRefs(store);
const draftUrl = ref(backendUrl.value);
const draftToken = ref(deviceToken.value);
const formError = ref<string>();
const recipes = ref<ProviderRecipe[]>([]);
const accessError = ref<string>();
const accessStatus = ref<string>();
const connecting = ref<string>();

async function refreshRecipes(): Promise<void> {
  recipes.value = await discoverRecipes(backendUrl.value, deviceToken.value);
}

function save(): void {
  try {
    store.configure(draftUrl.value, draftToken.value);
    formError.value = undefined;
    void store
      .check()
      .then(refreshRecipes)
      .catch(() => undefined);
  } catch (caught: unknown) {
    formError.value =
      caught instanceof Error ? caught.message : "Invalid connection settings";
  }
}

async function connect(provider: ProviderRecipe): Promise<void> {
  connecting.value = provider.providerId;
  accessError.value = undefined;
  accessStatus.value = undefined;
  try {
    await connectFromRecipe(
      backendUrl.value,
      deviceToken.value,
      provider,
      chromeArtifacts,
    );
    accessStatus.value = `${provider.providerId} connected`;
  } catch (caught: unknown) {
    accessError.value =
      caught instanceof Error ? caught.message : "Connection failed";
  } finally {
    connecting.value = undefined;
  }
}
</script>

<template>
  <main aria-labelledby="extension-title" class="shell">
    <header>
      <h1 id="extension-title">OpenBiliClaw</h1>
      <p>Local recommendation companion</p>
    </header>
    <section aria-labelledby="connection-title">
      <h2 id="connection-title">Backend connection</h2>
      <form @submit.prevent="save">
        <label>
          Backend URL
          <input
            v-model="draftUrl"
            name="backendUrl"
            aria-label="Backend URL"
            inputmode="url"
            autocomplete="url"
          />
        </label>
        <label>
          Extension token
          <input
            v-model="draftToken"
            name="deviceToken"
            aria-label="Extension token"
            type="password"
            autocomplete="off"
          />
        </label>
        <button type="submit">Save and connect</button>
      </form>
      <p role="status" aria-live="polite">Status: {{ state }}</p>
      <p v-if="formError ?? error" role="alert">{{ formError ?? error }}</p>
    </section>
    <section v-if="recipes.length" aria-labelledby="provider-access-title">
      <h2 id="provider-access-title">Provider access</h2>
      <article v-for="provider in recipes" :key="provider.providerId">
        <strong>{{ provider.providerId }}</strong>
        <button
          v-if="provider.recipe.warmup_url"
          type="button"
          class="secondary"
          @click="openWarmup(provider.recipe, chromeArtifacts)"
        >
          Open login
        </button>
        <button
          type="button"
          :disabled="connecting === provider.providerId"
          @click="connect(provider)"
        >
          {{ connecting === provider.providerId ? "Connecting…" : "Connect" }}
        </button>
      </article>
      <p v-if="accessStatus" role="status" aria-live="polite">
        {{ accessStatus }}
      </p>
      <p v-if="accessError" role="alert">{{ accessError }}</p>
    </section>
  </main>
</template>

<style scoped>
:global(*) {
  box-sizing: border-box;
}
:global(body) {
  margin: 0;
  color: #172033;
  background: #f5f7fb;
  font:
    14px/1.5 system-ui,
    sans-serif;
}
.shell {
  min-width: 320px;
  max-width: 520px;
  padding: 1rem;
}
header,
section {
  background: white;
  border-radius: 12px;
  padding: 1rem;
  margin-bottom: 0.75rem;
  box-shadow: 0 1px 4px #1720331f;
}
h1,
h2 {
  margin: 0 0 0.5rem;
}
p {
  margin: 0.25rem 0;
}
label {
  display: grid;
  gap: 0.25rem;
  margin-block: 0.75rem;
  font-weight: 600;
}
input,
button {
  min-height: 44px;
  border: 1px solid #79839a;
  border-radius: 8px;
  padding: 0.6rem;
}
button {
  color: white;
  background: #3156d3;
  font-weight: 700;
  cursor: pointer;
}
article {
  display: grid;
  gap: 0.5rem;
  margin-block: 0.75rem;
}
button.secondary {
  color: #3156d3;
  background: white;
}
button:disabled {
  cursor: wait;
  opacity: 0.65;
}
button:focus-visible,
input:focus-visible {
  outline: 3px solid #ffb000;
  outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
  * {
    scroll-behavior: auto !important;
  }
}
</style>
