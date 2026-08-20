<script setup lang="ts">
import { storeToRefs } from "pinia";
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  chromeArtifacts,
  connectFromRecipe,
  discoverRecipes,
  openWarmup,
  type ProviderRecipe,
} from "./access-flow";
import { useConnectionStore } from "./connection-store";
import { extensionIssue, type ExtensionIssue } from "./errors";

const { t, locale } = useI18n();
const store = useConnectionStore();
store.hydrate();
const { backendUrl, deviceToken, state, error } = storeToRefs(store);
const draftUrl = ref(backendUrl.value);
const draftToken = ref("");
const formError = ref<ExtensionIssue>();
const recipes = ref<ProviderRecipe[]>([]);
const accessError = ref<ExtensionIssue>();
const accessStatus = ref<string>();
const connecting = ref<string>();

async function refreshRecipes(): Promise<void> {
  recipes.value = await discoverRecipes(backendUrl.value, deviceToken.value);
  accessError.value = undefined;
}

function issueText(issue: ExtensionIssue): string {
  return t(`error.${issue.code}`, { status: issue.status ?? "" });
}

function save(): void {
  try {
    store.configure(draftUrl.value, draftToken.value || deviceToken.value);
    draftToken.value = "";
    formError.value = undefined;
    accessError.value = undefined;
    void store.check().then(async () => {
      if (state.value !== "connected") return;
      try {
        await refreshRecipes();
      } catch (caught: unknown) {
        accessError.value = extensionIssue(caught);
      }
    });
  } catch (caught: unknown) {
    formError.value = extensionIssue(caught);
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
    accessStatus.value = t("connected", { provider: provider.providerId });
  } catch (caught: unknown) {
    accessError.value = extensionIssue(caught);
  } finally {
    connecting.value = undefined;
  }
}
</script>

<template>
  <main aria-labelledby="extension-title" class="shell">
    <header>
      <div>
        <h1 id="extension-title">{{ t("title") }}</h1>
        <p>{{ t("tagline") }}</p>
      </div>
      <label class="language-control">
        <span>{{ t("locale.label") }}</span>
        <select v-model="locale" name="locale" :aria-label="t('locale.label')">
          <option value="en">{{ t("locale.en") }}</option>
          <option value="zh-CN">{{ t("locale.zhCN") }}</option>
          <option value="zh-TW">{{ t("locale.zhTW") }}</option>
        </select>
      </label>
    </header>
    <section aria-labelledby="connection-title">
      <h2 id="connection-title">{{ t("connection") }}</h2>
      <form @submit.prevent="save">
        <label>
          {{ t("backendUrl") }}
          <input
            v-model="draftUrl"
            name="backendUrl"
            :aria-label="t('backendUrl')"
            inputmode="url"
            type="url"
            required
            autocomplete="url"
            :placeholder="t('backendPlaceholder')"
            aria-describedby="backend-help"
          />
          <small id="backend-help">{{ t("backendHelp") }}</small>
        </label>
        <label>
          {{ t("token") }}
          <input
            v-model="draftToken"
            name="deviceToken"
            :aria-label="t('token')"
            type="password"
            :required="!deviceToken"
            autocomplete="new-password"
            :placeholder="t('tokenPlaceholder')"
            aria-describedby="token-help"
          />
          <small id="token-help">{{ t("tokenHelp") }}</small>
        </label>
        <button type="submit">{{ t("save") }}</button>
      </form>
      <p role="status" aria-live="polite">
        {{ t("status", { state: t(`state.${state}`) }) }}
      </p>
      <p v-if="formError ?? error" role="alert">
        {{ issueText((formError ?? error)!) }}
      </p>
    </section>
    <section
      v-if="recipes.length || accessError || accessStatus"
      aria-labelledby="provider-access-title"
    >
      <h2 id="provider-access-title">{{ t("access") }}</h2>
      <article v-for="provider in recipes" :key="provider.providerId">
        <strong>{{ provider.providerId }}</strong>
        <button
          v-if="provider.recipe.warmup_url"
          type="button"
          class="secondary"
          @click="openWarmup(provider.recipe, chromeArtifacts)"
        >
          {{ t("openLogin") }}
        </button>
        <button
          type="button"
          :disabled="connecting === provider.providerId"
          @click="connect(provider)"
        >
          {{
            connecting === provider.providerId ? t("connecting") : t("connect")
          }}
        </button>
      </article>
      <p v-if="accessStatus" role="status" aria-live="polite">
        {{ accessStatus }}
      </p>
      <p v-if="accessError" role="alert">{{ issueText(accessError) }}</p>
    </section>
  </main>
</template>

<style scoped>
:global(*) {
  box-sizing: border-box;
}
:global(body) {
  margin: 0;
  color: #1f1e1b;
  background: #f5f4ed;
  font:
    14px/1.5 system-ui,
    sans-serif;
}
.shell {
  min-width: 320px;
  max-width: 520px;
  padding: 0.9rem;
}
header,
section {
  margin-bottom: 0.75rem;
  border: 1px solid #dfdcd2;
  border-radius: 12px;
  padding: 1rem;
  background: #fffefa;
  box-shadow:
    0 1px 2px #1f1e1b0d,
    0 6px 18px #1f1e1b0a;
}
header {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 1rem;
}
h1,
h2 {
  margin: 0 0 0.4rem;
  line-height: 1.2;
}
h1 {
  font-size: 1.25rem;
}
h2 {
  font-size: 1rem;
}
p {
  margin: 0.3rem 0;
}
form {
  display: grid;
  gap: 0.15rem;
}
label {
  display: grid;
  gap: 0.3rem;
  margin-block: 0.65rem;
  font-weight: 650;
}
small {
  color: #716d64;
  font-size: 0.75rem;
  font-weight: 450;
  line-height: 1.45;
}
input,
select,
button {
  width: 100%;
  min-width: 0;
  min-height: 44px;
  border: 1px solid #b8b3a8;
  border-radius: 8px;
  padding: 0.6rem 0.7rem;
  background: #fffefa;
  color: #1f1e1b;
  font: inherit;
}
input:hover,
select:hover {
  border-color: #716d64;
}
input:user-invalid {
  border-color: #b4232d;
}
button {
  border-color: #2563a6;
  color: white;
  background: #2563a6;
  font-weight: 700;
  cursor: pointer;
}
button:hover:not(:disabled) {
  background: #174b7a;
}
.language-control {
  width: min(10rem, 44%);
  margin: 0;
  font-size: 0.75rem;
}
.language-control select {
  min-height: 38px;
  padding-block: 0.4rem;
}
article {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.5rem;
  margin-block: 0.75rem;
}
article button {
  width: auto;
}
button.secondary {
  color: #174b7a;
  background: #fffefa;
}
button:disabled {
  cursor: wait;
  opacity: 0.65;
}
[role="alert"] {
  border-left: 3px solid #b4232d;
  padding-left: 0.65rem;
  color: #8e1c24;
}
button:focus-visible,
input:focus-visible,
select:focus-visible {
  outline: 3px solid #2563a6;
  outline-offset: 2px;
  border-color: #2563a6;
}
@media (max-width: 360px) {
  header {
    align-items: stretch;
    flex-direction: column;
  }
  .language-control {
    width: 100%;
  }
  article {
    grid-template-columns: minmax(0, 1fr);
  }
  article button {
    width: 100%;
  }
}
@media (prefers-reduced-motion: reduce) {
  * {
    scroll-behavior: auto !important;
  }
}
</style>
