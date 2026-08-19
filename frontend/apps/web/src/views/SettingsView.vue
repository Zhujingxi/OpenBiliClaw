<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { WebApi } from "../services/api";
import { useModelsStore } from "../stores/models";
import { usePreferencesStore } from "../stores/preferences";

const injectedApi = inject<WebApi>("api");
if (injectedApi === undefined) throw new Error("WebApi was not provided");
const api: WebApi = injectedApi;
const preferences = usePreferencesStore();
const models = useModelsStore();
const query = ref("");
const providerId = ref("");
const modelName = ref("");
const endpoint = ref("");
const apiKey = ref("");
const custom = ref(false);
const customProvider = ref("");
const customProtocol = ref<"openai" | "anthropic" | "google" | "openrouter">(
  "openai",
);
const customContextTokens = ref(128_000);
const customTools = ref(true);
const customStructuredOutput = ref(false);
const customVision = ref(false);
const customStreaming = ref(true);
const customReasoning = ref(false);
const saved = ref(false);
const selectedProvider = computed(() =>
  models.providers.find((provider) => provider.id === providerId.value),
);
const filteredProviders = computed(() => {
  const needle = query.value.trim().toLowerCase();
  const matches = needle
    ? models.providers.filter(
        (provider) =>
          provider.id === providerId.value ||
          provider.id.toLowerCase().includes(needle) ||
          provider.name.toLowerCase().includes(needle) ||
          provider.models.some(
            (model) =>
              model.id.toLowerCase().includes(needle) ||
              model.name.toLowerCase().includes(needle),
          ),
      )
    : [...models.providers];
  return matches.sort(
    (left, right) =>
      Number(right.id === providerId.value) -
      Number(left.id === providerId.value),
  );
});
watch(
  () => models.current,
  (current) => {
    if (current === undefined) return;
    providerId.value = current.current.model.provider;
    modelName.value = current.current.model.model_name;
    endpoint.value = current.current.model.endpoint ?? "";
  },
  { immediate: true },
);
watch(providerId, (next) => {
  if (!next) {
    modelName.value = "";
    return;
  }
  if (
    !selectedProvider.value?.models.some(
      (model) => model.id === modelName.value,
    )
  ) {
    modelName.value = selectedProvider.value?.models[0]?.id ?? "";
  }
});
function chooseProvider(id: string): void {
  custom.value = false;
  providerId.value = id;
}
async function save(): Promise<void> {
  const provider = custom.value
    ? customProvider.value.trim()
    : providerId.value;
  if (!provider || !modelName.value) return;
  const request = {
    provider,
    model_name: modelName.value,
    ...(endpoint.value.trim() ? { endpoint: endpoint.value.trim() } : {}),
    ...(apiKey.value ? { api_key: apiKey.value } : {}),
    ...(custom.value
      ? {
          protocol: customProtocol.value,
          capabilities: {
            tools: customTools.value,
            structured_output: customStructuredOutput.value,
            vision: customVision.value,
            context_tokens: customContextTokens.value,
            streaming: customStreaming.value,
            reasoning: customReasoning.value,
          },
        }
      : {}),
  };
  saved.value = await models.save(api, request);
  if (saved.value) apiKey.value = "";
}
onMounted(() => void models.load(api));
onBeforeUnmount(() => models.cancel());
</script>

<template>
  <section class="settings-page">
    <div class="page-heading">
      <div class="page-heading-copy">
        <p class="eyebrow">Configuration</p>
        <h1 tabindex="-1">Settings</h1>
        <p>
          Choose your AI runtime, tune the interface, and see what is active.
        </p>
      </div>
      <a class="button-secondary settings-link" href="#/providers">
        Manage content sources
      </a>
    </div>

    <p v-if="models.phase === 'loading'" aria-live="polite">
      Loading model catalog…
    </p>
    <p v-else-if="models.phase === 'error'" role="alert">
      {{ models.error }}
    </p>
    <p v-else-if="models.phase === 'empty'" aria-live="polite">
      No catalog providers are available.
    </p>

    <div v-else-if="models.phase === 'success'" class="settings-layout">
      <aside
        v-if="models.current"
        aria-labelledby="current-model-heading"
        class="surface-card current-configuration"
      >
        <div class="surface-card-header">
          <div>
            <p class="eyebrow">In use</p>
            <h2 id="current-model-heading">Active runtime</h2>
          </div>
          <span
            class="status-badge"
            :class="
              models.current.current.model.secret_configured
                ? 'status-connected'
                : 'status-warning'
            "
          >
            {{
              models.current.current.model.secret_configured
                ? "Ready"
                : "Key needed"
            }}
          </span>
        </div>
        <dl>
          <dt>Model provider</dt>
          <dd>{{ models.current.current.model.provider }}</dd>
          <dt>Model</dt>
          <dd>
            {{ models.current.current.model.model_name || "Not configured" }}
          </dd>
          <dt>Model credential</dt>
          <dd>
            {{
              models.current.current.model.secret_configured
                ? "Configured"
                : "Not configured"
            }}
          </dd>
          <dt>Restart required</dt>
          <dd>{{ models.current.restart_required ? "Yes" : "No" }}</dd>
          <dt>Reloaded in this process</dt>
          <dd>{{ models.current.reloaded ? "Yes" : "No" }}</dd>
        </dl>
        <div class="embedding-summary">
          <p class="eyebrow">Embedding</p>
          <dl>
            <dt>Provider</dt>
            <dd>
              {{
                models.current.current.embedding.provider || "Not configured"
              }}
            </dd>
            <dt>Model</dt>
            <dd>
              {{
                models.current.current.embedding.model_name || "Not configured"
              }}
            </dd>
            <dt>Endpoint</dt>
            <dd>
              {{ models.current.current.embedding.endpoint || "Default" }}
            </dd>
            <dt>Credential</dt>
            <dd>
              {{
                models.current.current.embedding.secret_configured
                  ? "Configured"
                  : "Not configured"
              }}
            </dd>
          </dl>
        </div>
      </aside>

      <form class="surface-card model-form" @submit.prevent="save">
        <div class="surface-card-header">
          <div>
            <p class="eyebrow">AI provider</p>
            <h2>Provider & model</h2>
            <p>Browse the catalog or connect any compatible endpoint.</p>
          </div>
        </div>

        <details class="settings-section provider-browser" open>
          <summary class="settings-section-summary">
            <span>
              <strong>Choose provider</strong>
              <small>Search the catalog or use a custom endpoint.</small>
            </span>
            <span class="section-count"
              >{{ filteredProviders.length }} shown</span
            >
          </summary>
          <div class="settings-section-content">
            <div class="model-toolbar">
              <div class="field">
                <label for="model-search">Search providers and models</label>
                <input
                  id="model-search"
                  v-model="query"
                  type="search"
                  placeholder="Try OpenAI, Claude, DeepSeek…"
                />
              </div>
              <label class="check-row custom-toggle">
                <input v-model="custom" type="checkbox" />
                Custom provider
              </label>
            </div>

            <div
              v-if="!custom"
              class="provider-gallery"
              aria-label="Model providers"
            >
              <button
                v-for="provider in filteredProviders"
                :key="provider.id"
                type="button"
                class="provider-option"
                :class="{ selected: provider.id === providerId }"
                :aria-pressed="provider.id === providerId"
                @click="chooseProvider(provider.id)"
              >
                <span class="provider-monogram" aria-hidden="true">
                  {{ provider.name.slice(0, 1).toUpperCase() }}
                </span>
                <span>
                  <strong>{{ provider.name }}</strong>
                  <small
                    >{{ provider.models.length }} models ·
                    {{ provider.protocol }}</small
                  >
                </span>
                <span
                  v-if="provider.id === providerId"
                  class="selection-mark"
                  aria-hidden="true"
                  >✓</span
                >
              </button>
            </div>
          </div>
        </details>

        <div class="form-grid configuration-fields">
          <div class="configuration-heading field-wide">
            <strong>Configure model</strong>
            <small>{{
              custom
                ? "Custom endpoint"
                : selectedProvider?.name || "Select a provider"
            }}</small>
          </div>
          <template v-if="!custom">
            <div class="field">
              <label for="model-provider">Provider</label>
              <select id="model-provider" v-model="providerId" required>
                <option value="" disabled>Select a provider</option>
                <option
                  v-for="provider in filteredProviders"
                  :key="provider.id"
                  :value="provider.id"
                >
                  {{ provider.name }} ({{ provider.id }})
                </option>
              </select>
            </div>
          </template>
          <template v-else>
            <div class="field">
              <label for="custom-provider">Custom provider ID</label>
              <input
                id="custom-provider"
                v-model="customProvider"
                required
                placeholder="my-provider"
              />
            </div>
            <div class="field">
              <label for="custom-protocol">API protocol</label>
              <select id="custom-protocol" v-model="customProtocol">
                <option value="openai">OpenAI compatible</option>
                <option value="anthropic">Anthropic</option>
                <option value="google">Google</option>
                <option value="openrouter">OpenRouter</option>
              </select>
            </div>
          </template>

          <div class="field">
            <label for="model-name">Model</label>
            <input
              v-if="custom"
              id="model-name"
              v-model="modelName"
              required
              placeholder="Model ID"
            />
            <select v-else id="model-name" v-model="modelName" required>
              <option value="" disabled>Select a model</option>
              <option
                v-for="model in selectedProvider?.models ?? []"
                :key="model.id"
                :value="model.id"
              >
                {{ model.name }} ({{ model.id }})
              </option>
            </select>
          </div>

          <div class="field">
            <label for="model-endpoint">
              {{ custom ? "Endpoint" : "Endpoint override" }}
            </label>
            <input
              id="model-endpoint"
              v-model="endpoint"
              type="url"
              :required="custom"
              :placeholder="
                custom ? 'https://api.example.com' : 'Use catalog default'
              "
            />
          </div>

          <div class="field field-wide">
            <label for="model-api-key">API key</label>
            <input
              id="model-api-key"
              v-model="apiKey"
              type="password"
              autocomplete="new-password"
              :placeholder="
                models.current?.current.model.secret_configured
                  ? 'Configured — leave blank to keep'
                  : 'Paste a write-only API key'
              "
            />
            <p class="field-hint">
              Stored in the local credential vault and never returned to this
              page.
            </p>
          </div>

          <fieldset v-if="custom" class="capabilities">
            <legend>Provider capabilities</legend>
            <div class="field">
              <label for="custom-context">Context token limit</label>
              <input
                id="custom-context"
                v-model.number="customContextTokens"
                type="number"
                min="0"
                required
              />
            </div>
            <div class="capability-options">
              <label
                ><input v-model="customTools" type="checkbox" /> Tools</label
              >
              <label>
                <input v-model="customStructuredOutput" type="checkbox" />
                Structured output
              </label>
              <label
                ><input v-model="customVision" type="checkbox" /> Vision</label
              >
              <label
                ><input v-model="customStreaming" type="checkbox" />
                Streaming</label
              >
              <label
                ><input v-model="customReasoning" type="checkbox" />
                Reasoning</label
              >
            </div>
          </fieldset>

          <p
            v-if="selectedProvider && !custom"
            class="model-metadata field-wide"
          >
            <strong>{{ selectedProvider.protocol }}</strong> protocol · expected
            key:
            {{ selectedProvider.env.join(", ") || "none" }}
          </p>
          <button type="submit" :disabled="models.savePhase === 'loading'">
            {{
              models.savePhase === "loading" ? "Saving…" : "Save and use model"
            }}
          </button>
          <p v-if="models.savePhase === 'error'" role="alert">
            {{ models.error }}
          </p>
          <p v-if="saved" role="status" aria-live="polite">
            Saved.
            {{
              models.current?.restart_required
                ? "Restart OpenBiliClaw to apply this model."
                : "The model is active."
            }}
          </p>
        </div>
      </form>
    </div>

    <section aria-labelledby="display-settings-heading" class="appearance-card">
      <div class="surface-card-header">
        <div>
          <p class="eyebrow">Interface</p>
          <h2 id="display-settings-heading">Appearance</h2>
          <p>Keep the workspace comfortable without hiding information.</p>
        </div>
      </div>
      <div class="appearance-controls">
        <div class="field">
          <label for="density">Display density</label>
          <select id="density" v-model="preferences.density">
            <option value="comfortable">Comfortable</option>
            <option value="compact">Compact</option>
          </select>
        </div>
        <label class="check-row">
          <input v-model="preferences.reducedMotion" type="checkbox" />
          Reduce motion
        </label>
      </div>
    </section>
  </section>
</template>

<style scoped>
.settings-link {
  display: inline-flex;
  align-items: center;
  min-height: 2.5rem;
  padding: 0.55rem 0.9rem;
  border-radius: var(--radius-sm);
  color: var(--foreground);
  font-size: 0.82rem;
  font-weight: 650;
  text-decoration: none;
}
.settings-layout {
  display: grid;
  grid-template-columns: minmax(15rem, 0.72fr) minmax(0, 2fr);
  gap: 1rem;
  align-items: start;
}
.current-configuration {
  position: sticky;
  top: 5rem;
}
.embedding-summary {
  margin-top: 1.2rem;
  border-top: 1px solid var(--border);
  padding-top: 1rem;
}
.model-form {
  display: grid;
  grid-template-columns: minmax(16rem, 0.72fr) minmax(20rem, 1.28fr);
  gap: 1rem;
  align-items: start;
}
.model-form > .surface-card-header {
  grid-column: 1 / -1;
  margin-bottom: 0;
}
.settings-section {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--card);
}
.settings-section-summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 0.75rem;
  align-items: center;
  padding: 0.85rem 1rem;
  list-style: none;
}
.settings-section-summary::-webkit-details-marker {
  display: none;
}
.settings-section-summary::after {
  content: "+";
  color: var(--muted-foreground);
  font-size: 1.1rem;
}
.settings-section[open] > .settings-section-summary::after {
  content: "−";
}
.settings-section-summary > span:first-child {
  display: grid;
  gap: 0.15rem;
}
.settings-section-summary small,
.section-count {
  color: var(--muted-foreground);
  font-size: 0.7rem;
  font-weight: 550;
}
.settings-section-content {
  display: grid;
  gap: 0.75rem;
  padding: 0 1rem 1rem;
}
.model-toolbar {
  display: grid;
  gap: 0.75rem;
}
.custom-toggle {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0 0.75rem;
  background: var(--muted);
}
.provider-gallery {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0.55rem;
  max-height: 26rem;
  overflow: auto;
  padding: 0.15rem;
}
.provider-option {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.65rem;
  min-height: 4.2rem;
  border-color: var(--border);
  padding: 0.65rem;
  background: var(--card);
  color: var(--foreground);
  text-align: left;
  box-shadow: none;
}
.provider-option:hover:not(:disabled) {
  border-color: #bdb9ae;
  background: var(--secondary);
}
.provider-option.selected {
  border-color: var(--brand);
  background: var(--brand-soft);
}
.provider-option > span:nth-child(2) {
  display: grid;
  gap: 0.15rem;
  min-width: 0;
}
.provider-option strong,
.provider-option small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.provider-option strong {
  font-size: 0.8rem;
}
.provider-option small {
  font-size: 0.67rem;
}
.provider-monogram {
  display: grid;
  place-items: center;
  width: 2rem;
  height: 2rem;
  border-radius: 0.6rem;
  background: var(--primary);
  color: var(--primary-foreground);
  font-size: 0.72rem;
  font-weight: 800;
}
.selection-mark {
  color: var(--brand-strong);
  font-weight: 800;
}
.configuration-fields {
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1rem;
}
.configuration-heading {
  display: grid;
  gap: 0.15rem;
}
.configuration-heading small {
  color: var(--muted-foreground);
  font-size: 0.7rem;
  font-weight: 550;
}
.capabilities {
  display: grid;
  gap: 0.9rem;
}
.capability-options {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
  gap: 0.65rem;
}
.capability-options label {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  font-weight: 550;
}
.model-metadata {
  margin: 0;
  border-radius: var(--radius-sm);
  padding: 0.65rem 0.75rem;
  background: var(--muted);
  color: var(--muted-foreground);
  font-size: 0.76rem;
}
.appearance-controls {
  display: grid;
  grid-template-columns: minmax(12rem, 20rem) auto;
  gap: 1rem;
  align-items: end;
}
@media (max-width: 68rem) {
  .settings-layout,
  .model-form {
    grid-template-columns: minmax(0, 1fr);
  }
  .model-form {
    order: -1;
  }
  .current-configuration {
    position: static;
  }
}
@media (max-width: 36rem) {
  .model-toolbar,
  .appearance-controls {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
