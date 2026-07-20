// Reproduction of review findings 1+2 against the setup wizard's usage of the
// shared model-config state module. Run: node tests/js/repro-review-findings.mjs
import assert from "node:assert/strict";
import * as state from "../../src/openbiliclaw/web/shared/model-config-state.js";

function credentialStatus(overrides = {}) {
  return {
    source: "none",
    configured: false,
    env_name: "",
    credential_ref: "",
    oauth_logged_in: false,
    ...overrides,
  };
}

const snapshot = {
  revision: "rev-1",
  source: "native",
  models: {
    schema_version: 1,
    chat: {
      connections: [
        {
          id: "chat-primary",
          name: "DeepSeek",
          type: "openai_compatible",
          preset: "deepseek",
          model: "deepseek-chat",
          base_url: "https://api.deepseek.com/v1",
          credential: credentialStatus(),
          api_mode: "chat_completions",
          reasoning_effort: "",
          http_referer: "",
          x_title: "",
          num_ctx: 0,
          probe: null,
          circuit: { state: "closed" },
        },
      ],
      concurrency: 4,
      timeout_seconds: 300,
    },
    embedding: {
      enabled: true,
      settings: {
        model: "bge-m3",
        output_dimensionality: 1024,
        similarity_threshold: 0.55,
        multimodal_enabled: false,
      },
      providers: [
        {
          id: "embedding-primary",
          name: "Ollama",
          type: "ollama",
          preset: "ollama",
          base_url: "http://127.0.0.1:11434",
          credential: credentialStatus(),
          probe: null,
          circuit: { state: "closed" },
        },
      ],
    },
  },
};

// ── Finding 1: wizard credential click then typing ─────────────────────
// The wizard click handler calls updateRouteField(..., "credential",
// {action: "set", value: ""}); the FIXED input handler merges the current
// credential (retaining the action) with the typed value — mirroring
// setup/index.html input listeners after the repair.
let modelState = state.hydrateModelConfig(snapshot);
const record = state.selectedRecord(modelState, "chat");

// 1. user clicks "输入 API Key" (action=set)
modelState = state.updateRouteField(modelState, "chat", record.id, "credential", {
  action: "set",
  value: "",
});
// 2. user types "sk-test" — fixed wizard input handler merges the credential
const current = state.selectedRecord(modelState, "chat");
modelState = state.updateRouteField(modelState, "chat", current.id, "credential", {
  ...current.credential,
  value: "sk-test",
});
const afterTyping = state.selectedRecord(modelState, "chat").credential;
console.log("finding1 credential_after_typing =", JSON.stringify(afterTyping));
assert.equal(afterTyping.action, "set", "action must remain set after typing");
assert.equal(afterTyping.value, "sk-test", "typed value must be retained");

// ── Finding 2: embedding Skip with existing providers ──────────────────
// setEmbeddingEnabled(false) only flips enabled; toModelConfigPayload must
// never serialize enabled=false WITH providers (backend rejects it as
// embedding_disabled_with_providers).
let skipState = state.hydrateModelConfig(snapshot);
skipState = state.updateRouteSetting(skipState, "embedding", "enabled", false);
const payload = state.toModelConfigPayload(skipState);
console.log(
  "finding2 disabled_embedding_payload_providers =",
  JSON.stringify(payload.models.embedding.providers),
  "enabled =",
  payload.models.embedding.enabled,
);
assert.equal(payload.models.embedding.enabled, false);
assert.deepEqual(
  payload.models.embedding.providers,
  [],
  "disabled embedding route must serialize zero providers",
);
// Client-side state must still retain the provider for same-session re-enable.
assert.equal(
  skipState.models.embedding.providers.length,
  1,
  "client state keeps the provider so re-enable within the session restores it",
);

console.log("repro OK: findings 1+2 fixed");
