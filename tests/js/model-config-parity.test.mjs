// @ts-check
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import * as popupState from "../../extension/popup/popup-model-config-state.js";
import * as webState from "../../src/openbiliclaw/web/shared/model-config-state.js";

/**
 * The popup module is a byte-synced copy of the web module (asserted below),
 * so both namespaces expose the same loose-record API. Typed as any here
 * because the pre-TS-conversion .js source exports no types.
 * @type {any}
 */
const popupImpl = popupState;
/** @type {any} */
const webImpl = webState;

const IMPLEMENTATIONS = [
  ["web", webImpl],
  ["extension", popupImpl],
];

test("extension popup state module is a synced copy of the shared source", () => {
  const root = resolve(import.meta.dirname, "..", "..");
  const source = readFileSync(
    resolve(root, "src/openbiliclaw/web/shared/model-config-state.js"),
    "utf-8",
  );
  const generated = readFileSync(
    resolve(root, "extension/popup/popup-model-config-state.js"),
    "utf-8",
  );
  assert.ok(
    generated.endsWith(source),
    "extension/popup/popup-model-config-state.js drifted from the shared source. "
      + "Regenerate with: node extension/scripts/sync-model-config-state.mjs",
  );
});

/**
 * @param {string} id
 * @param {Record<string, any>} [overrides]
 * @returns {Record<string, any>}
 */
function connection(id, overrides = {}) {
  return {
    id,
    name: `Connection ${id}`,
    type: "openai_compatible",
    preset: "custom",
    model: `model-${id}`,
    base_url: "https://example.test/v1",
    credential: {
      source: "inline",
      configured: true,
      env_name: "",
      credential_ref: "",
      oauth_logged_in: false,
    },
    api_mode: "chat_completions",
    reasoning_effort: "",
    http_referer: "",
    x_title: "",
    num_ctx: 0,
    probe: null,
    circuit: { state: "closed" },
    ...overrides,
  };
}

/**
 * @param {string} id
 * @param {Record<string, any>} [overrides]
 * @returns {Record<string, any>}
 */
function provider(id, overrides = {}) {
  const item = connection(id, overrides);
  for (const field of [
    "model",
    "api_mode",
    "reasoning_effort",
    "http_referer",
    "x_title",
    "num_ctx",
  ]) delete item[field];
  return item;
}

/**
 * @param {string[]} [ids]
 * @param {string} [revision]
 * @returns {Record<string, any>}
 */
function snapshot(ids = ["a", "b", "c"], revision = "revision-a") {
  return {
    revision,
    source: "native",
    models: {
      schema_version: 1,
      chat: {
        connections: ids.map((id) => connection(id)),
        concurrency: 4,
        timeout_seconds: 300,
      },
      embedding: {
        enabled: true,
        settings: {
          model: "bge-m3",
          output_dimensionality: 1024,
          similarity_threshold: 0.82,
          multimodal_enabled: false,
        },
        providers: [provider("embedding-a")],
      },
    },
    migration: { state: "none", confirmed: true, issues: [] },
    overrides: [],
  };
}

test("field-error vectors keep prototype-like stable IDs as own keys", () => {
  // Deliberately touching Object.prototype fields to prove the state module
  // keeps prototype-like stable IDs as own keys; typed via any since
  // Object.prototype has no declared num_ctx.
  const proto = /** @type {any} */ (Object.prototype);
  const previousPrototypeError = proto.num_ctx;
  try {
    for (const [name, implementation] of IMPLEMENTATIONS) {
      const state = implementation.mapServerFieldErrors(
        implementation.hydrateModelConfig(snapshot()),
        [{
          connection_id: "__proto__",
          path: "models.chat.connections.0.num_ctx",
          code: "invalid",
          message: "Invalid context window.",
        }],
      );

      assert.equal(
        Object.hasOwn(state.fieldErrors.byConnection, "__proto__"),
        true,
        `${name} must retain __proto__ as an own stable ID`,
      );
      assert.equal(
        state.fieldErrors.byConnection.__proto__.num_ctx.message,
        "Invalid context window.",
        name,
      );
      assert.equal(proto.num_ctx, previousPrototypeError, name);
    }
  } finally {
    if (previousPrototypeError === undefined) delete proto.num_ctx;
    else proto.num_ctx = previousPrototypeError;
  }
});

test("equal-revision remote vectors never create a false conflict", () => {
  for (const [name, implementation] of IMPLEMENTATIONS) {
    const base = implementation.hydrateModelConfig(snapshot(["a"], "revision-a"));
    const dirty = implementation.updateRouteField(
      base,
      "chat",
      "a",
      "model",
      "local-draft",
    );
    const received = implementation.receiveRemoteSnapshot(
      dirty,
      snapshot(["a"], "revision-a"),
      { force: true },
    );

    assert.equal(received.models.chat.connections[0].model, "local-draft", name);
    assert.equal(received.remoteUpdate, null, name);
  }
});

const TYPE_DESCRIPTOR = {
  id: "ollama",
  category: "local_runtime",
  fields: [
    { name: "model", capabilities: ["chat"], presets: [] },
    { name: "base_url", capabilities: ["chat"], presets: [] },
    { name: "num_ctx", capabilities: ["chat"], presets: [] },
  ],
  preset_definitions: [],
};

/** @type {Array<[string, (implementation: any) => any]>} */
const PARITY_VECTORS = [
  ["hydrate", (implementation) => implementation.hydrateModelConfig(snapshot())],
  ["append", (implementation) => implementation.appendRouteItem(
    implementation.hydrateModelConfig(snapshot(["a", "b"])),
    "chat",
    connection("c"),
  )],
  ["remove", (implementation) => implementation.removeRouteItem(
    implementation.hydrateModelConfig(snapshot(["a", "b", "c"])),
    "chat",
    "b",
  )],
  ["move up", (implementation) => implementation.moveRouteItem(
    implementation.hydrateModelConfig(snapshot(["a", "b", "c"])),
    "chat",
    "c",
    1,
  )],
  ["move down", (implementation) => implementation.moveRouteItem(
    implementation.hydrateModelConfig(snapshot(["a", "b", "c"])),
    "chat",
    "a",
    1,
  )],
  ["field update", (implementation) => {
    let state = implementation.hydrateModelConfig(snapshot(["a"]));
    state = implementation.updateRouteField(state, "chat", "a", "model", "updated-model");
    return implementation.updateRouteField(
      state,
      "chat",
      "a",
      "credential",
      { action: "set", value: "new-secret" },
    );
  }],
  ["type switch field-clearing", (implementation) => implementation.changeConnectionType(
    implementation.hydrateModelConfig(snapshot(["a"])),
    "chat",
    "a",
    TYPE_DESCRIPTOR,
    { confirmed: true, previousDescriptor: { category: "api_protocol" } },
  )],
  ["preset fill-only-empty", (implementation) => {
    let state = implementation.hydrateModelConfig(snapshot(["a"]));
    state = implementation.updateRouteField(
      state,
      "chat",
      "a",
      "base_url",
      "https://custom.test/v1",
    );
    return implementation.applyPreset(state, "chat", "a", {
      id: "openrouter",
      defaults: {
        base_url: "https://openrouter.ai/api/v1",
        api_mode: "responses",
        http_referer: "https://openbiliclaw.local",
      },
    }, {
      previousPreset: { id: "custom", defaults: { api_mode: "chat_completions" } },
    });
  }],
  ["payload construction", (implementation) => {
    let state = implementation.hydrateModelConfig(snapshot(["a"]));
    state = implementation.updateRouteField(
      state,
      "chat",
      "a",
      "credential",
      { action: "set", value: "new-secret" },
    );
    state = implementation.setMigrationResolution(
      state,
      "legacy-1",
      { action: "add_to_chat_route", position: 9 },
    );
    return implementation.toModelConfigPayload(state);
  }],
  ["remote conflict and revision", (implementation) => {
    const clean = implementation.hydrateModelConfig(snapshot(["a"], "revision-a"));
    const refreshed = implementation.receiveRemoteSnapshot(
      clean,
      snapshot(["remote"], "revision-b"),
    );
    const dirty = implementation.updateRouteField(clean, "chat", "a", "model", "local-model");
    return {
      refreshed,
      retained: implementation.receiveRemoteSnapshot(
        dirty,
        snapshot(["remote"], "revision-b"),
      ),
    };
  }],
  ["exact-probe fingerprint", (implementation) => {
    const initial = implementation.hydrateModelConfig(snapshot(["a", "b"]));
    const signature = implementation.createProbeSignature(initial, "chat", "a");
    const reordered = implementation.moveRouteItem(initial, "chat", "a", 1);
    const accepted = implementation.applyProbeResult(
      reordered,
      signature,
      { ok: true, connection_id: "a" },
    );
    const edited = implementation.updateRouteField(initial, "chat", "a", "model", "changed");
    return {
      signature,
      accepted,
      rejected: implementation.applyProbeResult(edited, signature, { ok: true }),
    };
  }],
];

for (const [name, run] of PARITY_VECTORS) {
  test(`web and extension match: ${name}`, () => {
    assert.deepEqual(run(popupState), run(webState));
  });
}
