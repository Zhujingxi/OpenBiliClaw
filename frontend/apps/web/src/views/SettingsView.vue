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
  if (!needle) return models.providers;
  return models.providers.filter(
    (provider) =>
      provider.id === providerId.value ||
      provider.id.toLowerCase().includes(needle) ||
      provider.name.toLowerCase().includes(needle) ||
      provider.models.some(
        (model) =>
          model.id.toLowerCase().includes(needle) ||
          model.name.toLowerCase().includes(needle),
      ),
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
  <section>
    <h1 tabindex="-1">Settings</h1>
    <section aria-labelledby="model-settings-heading">
      <h2 id="model-settings-heading">Model</h2>
      <p>
        Provider, endpoint, protocol, and capabilities come from models.dev. API
        keys are write-only and stored in the local credential vault.
      </p>
      <p v-if="models.phase === 'loading'" aria-live="polite">
        Loading model catalog…
      </p>
      <p v-else-if="models.phase === 'error'" role="alert">
        {{ models.error }}
      </p>
      <p v-else-if="models.phase === 'empty'" aria-live="polite">
        No catalog providers are available.
      </p>
      <template v-else-if="models.phase === 'success'">
        <section
          v-if="models.current"
          aria-labelledby="current-model-heading"
          class="current-configuration"
        >
          <h3 id="current-model-heading">Current configuration</h3>
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
          <h4>Embedding</h4>
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
        </section>
        <form @submit.prevent="save">
          <label for="model-search">Search catalog</label>
          <input
            id="model-search"
            v-model="query"
            type="search"
            placeholder="Provider or model"
          />
          <label>
            <input v-model="custom" type="checkbox" /> Configure a custom
            provider
          </label>
          <template v-if="!custom">
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
          </template>
          <template v-else>
            <label for="custom-provider">Custom provider ID</label>
            <input id="custom-provider" v-model="customProvider" required />
            <label for="custom-protocol">Protocol</label>
            <select id="custom-protocol" v-model="customProtocol">
              <option value="openai">OpenAI compatible</option>
              <option value="anthropic">Anthropic</option>
              <option value="google">Google</option>
              <option value="openrouter">OpenRouter</option>
            </select>
          </template>
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
          <fieldset v-if="custom" class="capabilities">
            <legend>Custom provider capabilities</legend>
            <label for="custom-context">Context token limit</label>
            <input
              id="custom-context"
              v-model.number="customContextTokens"
              type="number"
              min="0"
              required
            />
            <div class="capability-options">
              <label
                ><input v-model="customTools" type="checkbox" /> Tools</label
              >
              <label
                ><input v-model="customStructuredOutput" type="checkbox" />
                Structured output</label
              >
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
          <p v-if="selectedProvider && !custom" class="model-metadata">
            Protocol: {{ selectedProvider.protocol }} · Environment keys:
            {{ selectedProvider.env.join(", ") || "none" }}
          </p>
          <label for="model-endpoint">
            {{ custom ? "Endpoint" : "Endpoint override (optional)" }}
          </label>
          <input
            id="model-endpoint"
            v-model="endpoint"
            type="url"
            :required="custom"
            placeholder="Use catalog endpoint"
          />
          <label for="model-api-key">API key</label>
          <input
            id="model-api-key"
            v-model="apiKey"
            type="password"
            autocomplete="new-password"
            :placeholder="
              models.current?.current.model.secret_configured
                ? 'Configured — leave blank to keep'
                : 'Enter API key'
            "
          />
          <button type="submit" :disabled="models.savePhase === 'loading'">
            {{ models.savePhase === "loading" ? "Saving…" : "Save model" }}
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
        </form>
      </template>
    </section>
    <section aria-labelledby="display-settings-heading">
      <h2 id="display-settings-heading">Display</h2>
      <label for="density">Display density</label>
      <select id="density" v-model="preferences.density">
        <option value="comfortable">Comfortable</option>
        <option value="compact">Compact</option>
      </select>
      <label>
        <input v-model="preferences.reducedMotion" type="checkbox" /> Reduce
        motion
      </label>
    </section>
  </section>
</template>
<style scoped>
.capabilities {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(8rem, 1fr);
  gap: 0.75rem;
  align-items: center;
}
.capability-options {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
  gap: 0.75rem;
}
.capability-options label {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
.model-metadata {
  overflow-wrap: anywhere;
}
.current-configuration {
  padding: 1rem;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-strong);
  box-shadow: var(--shadow-sm);
}
.current-configuration dl {
  display: grid;
  grid-template-columns: minmax(8rem, 1fr) minmax(0, 2fr);
  gap: 0.35rem 1rem;
  margin: 0;
}
.current-configuration dd {
  margin: 0;
}
@media (max-width: 30rem) {
  .capabilities {
    grid-template-columns: minmax(0, 1fr);
  }
  .capability-options {
    grid-column: 1;
  }
}
</style>
