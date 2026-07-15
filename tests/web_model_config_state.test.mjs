import test from "node:test";
import assert from "node:assert/strict";

import {
  appendRouteItem,
  applyPreset,
  changeConnectionType,
  changePreset,
  hydrateModelConfig,
  mapServerFieldErrors,
  moveRouteItem,
  receiveRemoteSnapshot,
  removeRouteItem,
  selectRouteItem,
  setMigrationResolution,
  toModelConfigPayload,
  updateRouteField,
  updateRouteSetting,
} from "../src/openbiliclaw/web/shared/model-config-state.js";

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

function provider(id, overrides = {}) {
  const item = connection(id, overrides);
  delete item.model;
  delete item.api_mode;
  delete item.reasoning_effort;
  delete item.http_referer;
  delete item.x_title;
  delete item.num_ctx;
  return item;
}

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

const CHAT_DESCRIPTOR_FIELDS = [
  "model",
  "preset",
  "base_url",
  "credential",
  "api_mode",
  "reasoning_effort",
  "http_referer",
  "x_title",
  "num_ctx",
].map((name) => ({ name, capabilities: ["chat"], presets: [] }));

test("reorder changes order only and keeps credential actions", () => {
  const before = hydrateModelConfig(snapshot());
  const after = moveRouteItem(before, "chat", "c", 0);

  assert.deepEqual(after.models.chat.connections.map((item) => item.id), ["c", "a", "b"]);
  assert.equal(after.models.chat.connections[0].credential.action, "keep");
  assert.equal(after.models.chat.connections[0].model, "model-c");
  assert.equal(after.dirty, true);
});

test("chat caps at ten and cannot delete the last item", () => {
  const ten = hydrateModelConfig(snapshot(Array.from({ length: 10 }, (_, index) => `c-${index}`)));
  assert.throws(
    () => appendRouteItem(ten, "chat", connection("overflow")),
    /maximum 10/i,
  );

  const one = hydrateModelConfig(snapshot(["only"]));
  assert.throws(() => removeRouteItem(one, "chat", "only"), /at least one/i);
});

test("preset defaults fill only untouched fields", () => {
  let state = hydrateModelConfig(snapshot(["a"]));
  state = updateRouteField(state, "chat", "a", "base_url", "https://custom.test/v1");
  state = applyPreset(state, "chat", "a", {
    id: "openrouter",
    defaults: {
      base_url: "https://openrouter.ai/api/v1",
      api_mode: "responses",
      http_referer: "https://openbiliclaw.local",
    },
  }, {
    previousPreset: {
      id: "custom",
      defaults: { api_mode: "chat_completions" },
    },
  });

  const item = state.models.chat.connections[0];
  assert.equal(item.preset, "openrouter");
  assert.equal(item.base_url, "https://custom.test/v1");
  assert.equal(item.api_mode, "responses");
  assert.equal(item.http_referer, "https://openbiliclaw.local");
});

test("preset application preserves a persisted custom endpoint", () => {
  const before = hydrateModelConfig(snapshot(["a"]));
  const after = applyPreset(before, "chat", "a", {
    id: "openai",
    defaults: {
      base_url: "https://api.openai.com/v1",
      api_mode: "chat_completions",
    },
  }, {
    previousPreset: {
      id: "custom",
      defaults: { api_mode: "chat_completions" },
    },
  });

  assert.equal(after.models.chat.connections[0].base_url, "https://example.test/v1");
});

test("connection type changes require confirmation before clearing incompatible fields", () => {
  const before = hydrateModelConfig(snapshot(["a"]));
  const descriptor = {
    id: "ollama",
    fields: [
      { name: "model", capabilities: ["chat"], presets: [] },
      { name: "base_url", capabilities: ["chat"], presets: [] },
      { name: "num_ctx", capabilities: ["chat"], presets: [] },
    ],
  };

  const pending = changeConnectionType(before, "chat", "a", descriptor, { confirmed: false });
  assert.equal(pending.changed, false);
  assert.ok(pending.incompatibleFields.includes("api_mode"));
  assert.equal(pending.state.models.chat.connections[0].type, "openai_compatible");

  const accepted = changeConnectionType(before, "chat", "a", descriptor, { confirmed: true });
  const item = accepted.state.models.chat.connections[0];
  assert.equal(accepted.changed, true);
  assert.equal(item.type, "ollama");
  assert.equal(item.api_mode, "");
  assert.equal(item.id, "a");
  assert.equal(item.name, "Connection a");
});

test("connection type changes confirm and replace incompatible OAuth credential semantics", () => {
  const oauthSource = snapshot(["a"]);
  oauthSource.models.chat.connections[0] = connection("a", {
    type: "codex_oauth",
    preset: "",
    base_url: "",
    credential: {
      source: "oauth",
      configured: true,
      env_name: "",
      credential_ref: "codex",
      oauth_logged_in: true,
    },
    api_mode: "",
  });
  const oauthState = hydrateModelConfig(oauthSource);
  const oauthDescriptor = {
    id: "codex_oauth",
    category: "oauth",
    fields: [
      { name: "model", capabilities: ["chat"], presets: [] },
      { name: "credential", capabilities: ["chat"], presets: [] },
    ],
  };
  const apiKeyDescriptor = {
    id: "openai_compatible",
    category: "api_protocol",
    fields: [
      { name: "model", capabilities: ["chat"], presets: [] },
      { name: "base_url", capabilities: ["chat"], presets: [] },
      { name: "credential", capabilities: ["chat"], presets: [] },
      { name: "api_mode", capabilities: ["chat"], presets: [] },
    ],
  };

  const leavingOAuth = changeConnectionType(
    oauthState,
    "chat",
    "a",
    apiKeyDescriptor,
    { confirmed: false, previousDescriptor: oauthDescriptor },
  );
  assert.equal(leavingOAuth.changed, false);
  assert.ok(leavingOAuth.incompatibleFields.includes("credential"));
  const cleared = changeConnectionType(
    oauthState,
    "chat",
    "a",
    apiKeyDescriptor,
    { confirmed: true, previousDescriptor: oauthDescriptor },
  ).state.models.chat.connections[0];
  assert.deepEqual(cleared.credential, {
    action: "clear",
    value: "",
    status: {
      source: "none",
      configured: false,
      env_name: "",
      credential_ref: "",
      oauth_logged_in: false,
    },
  });

  const apiKeyState = hydrateModelConfig(snapshot(["a"]));
  const enteringOAuth = changeConnectionType(
    apiKeyState,
    "chat",
    "a",
    oauthDescriptor,
    { confirmed: false, previousDescriptor: apiKeyDescriptor },
  );
  assert.equal(enteringOAuth.changed, false);
  assert.ok(enteringOAuth.incompatibleFields.includes("credential"));
  const imported = changeConnectionType(
    apiKeyState,
    "chat",
    "a",
    oauthDescriptor,
    { confirmed: true, previousDescriptor: apiKeyDescriptor },
  ).state.models.chat.connections[0];
  assert.equal(imported.type, "codex_oauth");
  assert.equal(imported.credential.action, "keep");
  assert.equal(imported.credential.value, "");
});

test("preset changes require confirmation before clearing preset-only fields", () => {
  const source = snapshot(["a"]);
  source.models.chat.connections[0] = connection("a", {
    preset: "openrouter",
    http_referer: "https://custom.test",
    x_title: "Custom title",
  });
  const before = hydrateModelConfig(source);
  const descriptor = {
    id: "openai_compatible",
    fields: [
      { name: "preset", capabilities: ["chat"], presets: [] },
      { name: "model", capabilities: ["chat"], presets: [] },
      { name: "base_url", capabilities: ["chat"], presets: [] },
      { name: "credential", capabilities: ["chat"], presets: [] },
      { name: "api_mode", capabilities: ["chat"], presets: [] },
      { name: "http_referer", capabilities: ["chat"], presets: ["openrouter"] },
      { name: "x_title", capabilities: ["chat"], presets: ["openrouter"] },
    ],
  };
  const preset = {
    id: "openai",
    defaults: {
      base_url: "https://api.openai.com/v1",
      api_mode: "chat_completions",
    },
  };

  const pending = changePreset(before, "chat", "a", descriptor, preset, { confirmed: false });
  assert.equal(pending.changed, false);
  assert.deepEqual(pending.incompatibleFields, ["http_referer", "x_title"]);
  assert.equal(pending.state.models.chat.connections[0].preset, "openrouter");

  const accepted = changePreset(before, "chat", "a", descriptor, preset, { confirmed: true });
  assert.equal(accepted.changed, true);
  assert.equal(accepted.state.models.chat.connections[0].preset, "openai");
  assert.equal(accepted.state.models.chat.connections[0].http_referer, "");
  assert.equal(accepted.state.models.chat.connections[0].x_title, "");
  assert.equal(accepted.state.models.chat.connections[0].base_url, "https://example.test/v1");
});

test("selection stays on the same stable ID after reorder", () => {
  let state = hydrateModelConfig(snapshot());
  state = selectRouteItem(state, "chat", "c");
  state = moveRouteItem(state, "chat", "c", 0);

  assert.equal(state.selected.chat, "c");
  assert.equal(state.models.chat.connections[0].id, "c");
});

test("server field errors map by connection ID instead of array position", () => {
  const state = mapServerFieldErrors(hydrateModelConfig(snapshot()), [
    {
      connection_id: "b",
      path: "models.chat.connections.1.model",
      code: "required",
      message: "Choose a model.",
    },
    {
      connection_id: null,
      path: "models.chat.timeout_seconds",
      code: "too_small",
      message: "Timeout is too small.",
    },
  ]);

  assert.equal(state.fieldErrors.byConnection.b.model.message, "Choose a model.");
  assert.equal(state.fieldErrors.byConnection.b.model.code, "required");
  assert.equal(state.fieldErrors.global[0].message, "Timeout is too small.");
});

test("remote revision auto-hydrates only while clean", () => {
  const clean = hydrateModelConfig(snapshot(["a"], "revision-a"));
  const remote = snapshot(["remote"], "revision-b");
  const hydrated = receiveRemoteSnapshot(clean, remote);
  assert.equal(hydrated.revision, "revision-b");
  assert.equal(hydrated.models.chat.connections[0].id, "remote");
  assert.equal(hydrated.remoteUpdate, null);

  const dirty = updateRouteField(clean, "chat", "a", "model", "local-model");
  const retained = receiveRemoteSnapshot(dirty, remote);
  assert.equal(retained.revision, "revision-a");
  assert.equal(retained.models.chat.connections[0].model, "local-model");
  assert.equal(retained.remoteUpdate.latestRevision, "revision-b");
});

test("clean remote hydration preserves the active route and stable selection", () => {
  let clean = hydrateModelConfig(snapshot(["a", "b", "c"], "revision-a"));
  clean = selectRouteItem(clean, "chat", "c");
  clean.activeRoute = "embedding";

  const refreshed = receiveRemoteSnapshot(
    clean,
    snapshot(["b", "c", "a"], "revision-b"),
  );

  assert.equal(refreshed.activeRoute, "embedding");
  assert.equal(refreshed.selected.chat, "c");
  assert.deepEqual(refreshed.models.chat.connections.map((item) => item.id), ["b", "c", "a"]);
});

test("payload conversion is revisioned, strips health metadata, and never copies stored secrets", () => {
  const source = snapshot(["a"]);
  source.models.chat.connections[0].credential.value = "must-not-survive";
  let state = hydrateModelConfig(source);
  state = updateRouteField(state, "chat", "a", "credential", {
    action: "set",
    value: "new-secret",
  });
  state = setMigrationResolution(state, "legacy-1", {
    action: "accept_global_route",
  });

  const payload = toModelConfigPayload(state);
  const item = payload.models.chat.connections[0];
  assert.equal(payload.revision, "revision-a");
  assert.deepEqual(item.credential, { action: "set", value: "new-secret" });
  assert.equal("probe" in item, false);
  assert.equal("circuit" in item, false);
  assert.equal("model" in payload.models.embedding.providers[0], false);
  assert.deepEqual(payload.migration_resolutions["legacy-1"], {
    action: "accept_global_route",
  });
  assert.equal(JSON.stringify(hydrateModelConfig(source)).includes("must-not-survive"), false);
});

test("record fingerprint edits invalidate only that record's exact probe", () => {
  const identityChanges = [
    ["name", "Renamed connection"],
    ["type", "anthropic_compatible"],
    ["model", "changed-model"],
    ["preset", "openai"],
    ["base_url", "https://changed.example.test/v1"],
    ["credential", { action: "set", value: "new-secret" }],
    ["api_mode", "responses"],
    ["reasoning_effort", "high"],
    ["http_referer", "https://changed.example.test"],
    ["x_title", "Changed title"],
    ["num_ctx", 8192],
  ];

  for (const [field, value] of identityChanges) {
    const source = snapshot(["a", "b"]);
    source.models.chat.connections[0].probe = { ok: true, connection_id: "a" };
    source.models.chat.connections[1].probe = { ok: true, connection_id: "b" };
    const changed = updateRouteField(hydrateModelConfig(source), "chat", "a", field, value);

    assert.equal(changed.models.chat.connections[0].probe, null, field);
    assert.deepEqual(
      changed.models.chat.connections[1].probe,
      { ok: true, connection_id: "b" },
      field,
    );
  }

  const embeddingSource = snapshot();
  embeddingSource.models.embedding.providers = [
    provider("embedding-a", { probe: { ok: true, connection_id: "embedding-a" } }),
    provider("embedding-b", { probe: { ok: true, connection_id: "embedding-b" } }),
  ];
  const embeddingChanged = updateRouteField(
    hydrateModelConfig(embeddingSource),
    "embedding",
    "embedding-a",
    "base_url",
    "https://changed.example.test/v1",
  );
  assert.equal(embeddingChanged.models.embedding.providers[0].probe, null);
  assert.equal(embeddingChanged.models.embedding.providers[1].probe.ok, true);
});

test("preset and connection-type transitions invalidate the selected exact probe", () => {
  const source = snapshot(["a"]);
  source.models.chat.connections[0].probe = { ok: true, connection_id: "a" };
  const before = hydrateModelConfig(source);

  const presetChanged = applyPreset(before, "chat", "a", {
    id: "openai",
    capabilities: ["chat"],
    defaults: { api_mode: "responses" },
  });
  assert.equal(presetChanged.models.chat.connections[0].probe, null);

  const descriptor = {
    id: "anthropic_compatible",
    category: "api_protocol",
    fields: CHAT_DESCRIPTOR_FIELDS,
    preset_definitions: [
      { id: "anthropic", capabilities: ["chat"], defaults: {} },
    ],
  };
  const typeChanged = changeConnectionType(before, "chat", "a", descriptor, {
    confirmed: true,
    previousDescriptor: { category: "api_protocol" },
  });
  assert.equal(typeChanged.state.models.chat.connections[0].probe, null);
});

test("shared embedding setting edits invalidate every provider probe", () => {
  const changes = [
    ["model", "shared-model-v2"],
    ["output_dimensionality", 768],
    ["similarity_threshold", 0.73],
    ["multimodal_enabled", true],
  ];
  for (const [field, value] of changes) {
    const source = snapshot();
    source.models.embedding.providers = [
      provider("embedding-a", { probe: { ok: true, connection_id: "embedding-a" } }),
      provider("embedding-b", { probe: { ok: true, connection_id: "embedding-b" } }),
    ];
    const changed = updateRouteSetting(hydrateModelConfig(source), "embedding", field, value);
    assert.deepEqual(
      changed.models.embedding.providers.map((item) => item.probe),
      [null, null],
      field,
    );
  }
});

test("selection, reorder, route policy, and embedding enabled retain exact probes", () => {
  const source = snapshot(["a", "b"]);
  source.models.chat.connections[0].probe = { ok: true, connection_id: "a" };
  source.models.embedding.providers[0].probe = {
    ok: true,
    connection_id: "embedding-a",
  };
  let state = hydrateModelConfig(source);
  state = selectRouteItem(state, "chat", "b");
  state = moveRouteItem(state, "chat", "a", 1);
  for (const [field, value] of [["concurrency", 7], ["timeout_seconds", 480]]) {
    state = updateRouteSetting(state, "chat", field, value);
  }
  state = updateRouteSetting(state, "embedding", "enabled", false);

  assert.deepEqual(
    state.models.chat.connections.find((item) => item.id === "a").probe,
    { ok: true, connection_id: "a" },
  );
  assert.deepEqual(
    state.models.embedding.providers[0].probe,
    { ok: true, connection_id: "embedding-a" },
  );
});

test("migration chat additions receive unique append positions and reindex in issue order", () => {
  const source = snapshot(["a", "b", "c"]);
  source.migration = {
    state: "pending",
    confirmed: false,
    issues: [
      { id: "first", allowed_actions: ["add_to_chat_route", "discard"] },
      { id: "second", allowed_actions: ["add_to_chat_route", "discard"] },
      { id: "third", allowed_actions: ["add_to_chat_route", "discard"] },
    ],
  };
  let state = hydrateModelConfig(source);
  state = setMigrationResolution(state, "first", { action: "add_to_chat_route" });
  state = setMigrationResolution(state, "second", { action: "add_to_chat_route" });
  assert.equal(state.migration_resolutions.first.position, 4);
  assert.equal(state.migration_resolutions.second.position, 5);

  state = setMigrationResolution(state, "first", { action: "discard" });
  assert.equal("position" in state.migration_resolutions.first, false);
  assert.equal(state.migration_resolutions.second.position, 4);

  state = setMigrationResolution(state, "first", { action: "add_to_chat_route" });
  assert.equal(state.migration_resolutions.first.position, 4);
  assert.equal(state.migration_resolutions.second.position, 5);
});

test("hydration derives editable control locks from local override ancestors", () => {
  const source = snapshot();
  source.overrides = [
    { path: "models.chat.connections", source: "config.local.toml" },
    { path: "models.chat.concurrency", source: "config.local.toml" },
    { path: "models.embedding.enabled", source: "config.local.toml" },
    { path: "models.embedding.settings", source: "config.local.toml" },
  ];

  const state = hydrateModelConfig(source);
  assert.equal(
    state.overrideLocks["models.chat.connections"].source,
    "config.local.toml",
  );
  assert.equal(state.overrideLocks["models.chat.timeout_seconds"], null);
  assert.equal(state.overrideLocks["models.embedding.providers"], null);
  for (const field of [
    "model",
    "output_dimensionality",
    "similarity_threshold",
    "multimodal_enabled",
  ]) {
    assert.equal(
      state.overrideLocks[`models.embedding.settings.${field}`].path,
      "models.embedding.settings",
    );
  }
});

test("connection type chooses the first preset compatible with the active route", () => {
  const descriptor = {
    id: "mixed_protocol",
    category: "api_protocol",
    fields: CHAT_DESCRIPTOR_FIELDS,
    preset_definitions: [
      { id: "embedding-first", capabilities: ["embedding"], defaults: {} },
      { id: "chat-second", capabilities: ["chat"], defaults: {} },
    ],
  };
  const changed = changeConnectionType(
    hydrateModelConfig(snapshot(["a"])),
    "chat",
    "a",
    descriptor,
    { confirmed: true, previousDescriptor: { category: "api_protocol" } },
  );

  assert.equal(changed.state.models.chat.connections[0].preset, "chat-second");
});
