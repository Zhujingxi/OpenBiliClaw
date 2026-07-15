import test from "node:test";
import assert from "node:assert/strict";

import * as modelConfigState from "../src/openbiliclaw/web/shared/model-config-state.js";
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

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
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

test("server field errors cannot prototype-pollute through IDs or paths", () => {
  const previousPrototypeError = Object.prototype.num_ctx;
  const previousConstructorError = Object.constructor_error;
  try {
    const state = mapServerFieldErrors(hydrateModelConfig(snapshot()), [
      {
        connection_id: "__proto__",
        path: "models.chat.connections.0.num_ctx",
        code: "invalid",
        message: "Invalid context window.",
      },
      {
        connection_id: "constructor",
        path: "models.chat.connections.1.constructor_error",
        code: "invalid",
        message: "Invalid field.",
      },
    ]);

    assert.equal(Object.hasOwn(state.fieldErrors.byConnection, "__proto__"), true);
    assert.equal(Object.hasOwn(state.fieldErrors.byConnection, "constructor"), true);
    assert.equal(
      state.fieldErrors.byConnection.__proto__.num_ctx.message,
      "Invalid context window.",
    );
    assert.equal(
      state.fieldErrors.byConnection.constructor.constructor_error.message,
      "Invalid field.",
    );
    assert.equal(Object.prototype.num_ctx, previousPrototypeError);
    assert.equal(Object.constructor_error, previousConstructorError);
  } finally {
    if (previousPrototypeError === undefined) delete Object.prototype.num_ctx;
    else Object.prototype.num_ctx = previousPrototypeError;
    if (previousConstructorError === undefined) delete Object.constructor_error;
    else Object.constructor_error = previousConstructorError;
  }
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

test("in-flight probe results require the exact revision and draft but follow stable IDs", () => {
  assert.equal(typeof modelConfigState.createProbeSignature, "function");
  assert.equal(typeof modelConfigState.applyProbeResult, "function");

  const initial = hydrateModelConfig(snapshot(["a", "b"]));
  const signature = modelConfigState.createProbeSignature(initial, "chat", "a");
  let reordered = selectRouteItem(initial, "chat", "b");
  reordered = moveRouteItem(reordered, "chat", "a", 1);

  const accepted = modelConfigState.applyProbeResult(reordered, signature, {
    ok: true,
    connection_id: "a",
  });
  assert.equal(accepted.accepted, true);
  assert.equal(accepted.state.selected.chat, "b");
  assert.equal(
    accepted.state.models.chat.connections.find((item) => item.id === "a").probe.ok,
    true,
  );
  assert.equal(
    accepted.state.models.chat.connections.find((item) => item.id === "b").probe,
    null,
  );

  const edited = updateRouteField(initial, "chat", "a", "model", "new-model");
  const rejectedEdit = modelConfigState.applyProbeResult(edited, signature, { ok: true });
  assert.equal(rejectedEdit.accepted, false);
  assert.equal(rejectedEdit.state.models.chat.connections[0].probe, null);

  const revised = hydrateModelConfig(snapshot(["a", "b"], "revision-b"));
  const rejectedRevision = modelConfigState.applyProbeResult(revised, signature, { ok: true });
  assert.equal(rejectedRevision.accepted, false);
  assert.equal(rejectedRevision.state.models.chat.connections[0].probe, null);
});

test("embedding probe signatures include the shared model settings", () => {
  assert.equal(typeof modelConfigState.createProbeSignature, "function");
  assert.equal(typeof modelConfigState.applyProbeResult, "function");

  const initial = hydrateModelConfig(snapshot());
  const signature = modelConfigState.createProbeSignature(
    initial,
    "embedding",
    "embedding-a",
  );
  const changed = updateRouteSetting(initial, "embedding", "model", "other-vector-space");
  const result = modelConfigState.applyProbeResult(changed, signature, { ok: true });

  assert.equal(result.accepted, false);
  assert.equal(result.state.models.embedding.providers[0].probe, null);
});

test("desktop migration choices omit positions after route removals and reorders", () => {
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
  state = removeRouteItem(state, "chat", "b");
  state = moveRouteItem(state, "chat", "c", 0);

  assert.deepEqual(toModelConfigPayload(state).migration_resolutions, {
    first: { action: "add_to_chat_route" },
    second: { action: "add_to_chat_route" },
  });
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

test("an embedding provider override also locks enabled but not shared settings", () => {
  const source = snapshot();
  source.overrides = [
    { path: "models.embedding.providers", source: "config.local.toml" },
  ];

  const state = hydrateModelConfig(source);
  assert.deepEqual(state.overrideLocks["models.embedding.enabled"], {
    path: "models.embedding.providers",
    source: "config.local.toml",
  });
  assert.deepEqual(state.overrideLocks["models.embedding.providers"], {
    path: "models.embedding.providers",
    source: "config.local.toml",
  });
  assert.equal(state.overrideLocks["models.embedding.settings.model"], null);
});

test("a GET started before a successful PUT cannot replace the saved snapshot", async () => {
  const gate = modelConfigState.createLatestRequestGate();
  const pendingGet = deferred();
  const pendingPut = deferred();
  let saveInFlight = false;
  let visibleSnapshot = { revision: "draft" };

  const reload = modelConfigState.applyLatestSnapshotRequest({
    gate,
    request: () => pendingGet.promise,
    blocked: () => saveInFlight,
    apply: (snapshotValue) => {
      visibleSnapshot = snapshotValue;
    },
  });

  saveInFlight = true;
  gate.invalidate();
  const save = pendingPut.promise.then((snapshotValue) => {
    visibleSnapshot = snapshotValue;
    saveInFlight = false;
  });
  pendingPut.resolve({ revision: "saved" });
  await save;
  pendingGet.resolve({ revision: "stale" });

  assert.equal(await reload, false);
  assert.deepEqual(visibleSnapshot, { revision: "saved" });
});

test("a later GET supersedes an earlier GET", async () => {
  const gate = modelConfigState.createLatestRequestGate();
  const firstGet = deferred();
  const secondGet = deferred();
  let visibleSnapshot = null;
  const apply = (snapshotValue) => {
    visibleSnapshot = snapshotValue;
  };

  const firstReload = modelConfigState.applyLatestSnapshotRequest({
    gate,
    request: () => firstGet.promise,
    blocked: () => false,
    apply,
  });
  const secondReload = modelConfigState.applyLatestSnapshotRequest({
    gate,
    request: () => secondGet.promise,
    blocked: () => false,
    apply,
  });
  secondGet.resolve({ revision: "latest" });
  assert.equal(await secondReload, true);
  firstGet.resolve({ revision: "older" });

  assert.equal(await firstReload, false);
  assert.deepEqual(visibleSnapshot, { revision: "latest" });
});

test("a stale GET rejection is discarded but a current rejection propagates", async () => {
  const gate = modelConfigState.createLatestRequestGate();
  const staleGet = deferred();
  const staleReload = modelConfigState.applyLatestSnapshotRequest({
    gate,
    request: () => staleGet.promise,
    blocked: () => false,
    apply: () => assert.fail("a rejected request cannot apply"),
  });
  gate.invalidate();
  staleGet.reject(new Error("stale network error"));
  assert.equal(await staleReload, false);

  await assert.rejects(
    modelConfigState.applyLatestSnapshotRequest({
      gate,
      request: async () => {
        throw new Error("current network error");
      },
      blocked: () => false,
      apply: () => assert.fail("a rejected request cannot apply"),
    }),
    /current network error/,
  );
});

test("model operation gate serializes saves and invalidates pending probes", () => {
  assert.equal(typeof modelConfigState.createModelOperationGate, "function");
  const gate = modelConfigState.createModelOperationGate();

  const probeGeneration = gate.beginProbe();
  assert.equal(gate.probeInFlight, true);
  assert.equal(gate.isProbeCurrent(probeGeneration), true);

  const save = gate.beginSave();
  assert.equal(save.invalidatedProbe, true);
  assert.equal(gate.isProbeCurrent(probeGeneration), false);
  assert.equal(gate.beginSave(), null);
  assert.deepEqual(gate.controlState(), {
    editorLocked: true,
    saveDisabled: true,
    probeDisabled: true,
  });

  assert.equal(gate.finishSave(save.generation), true);
  assert.equal(gate.saveInFlight, false);
});

test("independent full load rechecks snapshot and descriptor ownership after settle", async () => {
  assert.equal(typeof modelConfigState.loadIndependentModelResources, "function");
  const snapshotGate = modelConfigState.createLatestRequestGate();
  const descriptorGate = modelConfigState.createLatestRequestGate();
  const firstSnapshot = deferred();
  const firstDescriptors = deferred();
  const latestDescriptors = deferred();
  let visibleSnapshot = null;
  let visibleDescriptors = null;

  const load = modelConfigState.loadIndependentModelResources({
    gate: snapshotGate,
    descriptorGate,
    snapshotRequest: () => firstSnapshot.promise,
    descriptorRequest: () => firstDescriptors.promise,
    blocked: () => false,
    applySnapshot: (value) => { visibleSnapshot = value; },
    installDescriptors: (value) => { visibleDescriptors = value; },
  });
  firstSnapshot.resolve({ revision: "revision-a" });
  firstDescriptors.resolve({ connection_types: [{ id: "stale" }] });
  await Promise.resolve();

  const descriptorReload = modelConfigState.applyLatestSnapshotRequest({
    gate: descriptorGate,
    request: () => latestDescriptors.promise,
    blocked: () => false,
    apply: (value) => { visibleDescriptors = value; },
  });
  latestDescriptors.resolve({ connection_types: [{ id: "latest" }] });

  assert.equal(await descriptorReload, true);
  assert.deepEqual(await load, { snapshotApplied: true, descriptorsInstalled: false });
  assert.deepEqual(visibleSnapshot, { revision: "revision-a" });
  assert.equal(visibleDescriptors.connection_types[0].id, "latest");
});

test("blocked snapshot is retained as remote while descriptor sibling still installs", async () => {
  const snapshotGate = modelConfigState.createLatestRequestGate();
  const descriptorGate = modelConfigState.createLatestRequestGate();
  const pendingSnapshot = deferred();
  const pendingDescriptors = deferred();
  let dirty = false;
  let remoteSnapshot = null;
  let visibleDescriptors = null;

  const load = modelConfigState.loadIndependentModelResources({
    gate: snapshotGate,
    descriptorGate,
    snapshotRequest: () => pendingSnapshot.promise,
    descriptorRequest: () => pendingDescriptors.promise,
    blocked: () => dirty,
    onSnapshotBlocked: (value) => { remoteSnapshot = value; },
    applySnapshot: () => assert.fail("a late snapshot cannot overwrite a dirty draft"),
    installDescriptors: (value) => { visibleDescriptors = value; },
  });
  dirty = true;
  pendingSnapshot.resolve({ revision: "remote" });
  pendingDescriptors.resolve({ connection_types: [{ id: "openai_compatible" }] });

  assert.deepEqual(await load, { snapshotApplied: false, descriptorsInstalled: true });
  assert.deepEqual(remoteSnapshot, { revision: "remote" });
  assert.equal(visibleDescriptors.connection_types[0].id, "openai_compatible");
});

test("a newer reload supersedes initial load while descriptors still install", async () => {
  const gate = modelConfigState.createLatestRequestGate();
  const initialGet = deferred();
  const descriptorGet = deferred();
  const reloadGet = deferred();
  let visibleSnapshot = null;
  let installedDescriptors = null;

  const initialLoad = modelConfigState.applyLatestSnapshotRequest({
    gate,
    request: async () => {
      const [snapshotValue, descriptorValue] = await Promise.all([
        initialGet.promise,
        descriptorGet.promise,
      ]);
      installedDescriptors = descriptorValue;
      return snapshotValue;
    },
    blocked: () => false,
    apply: (snapshotValue) => {
      visibleSnapshot = snapshotValue;
    },
  });
  const reload = modelConfigState.applyLatestSnapshotRequest({
    gate,
    request: () => reloadGet.promise,
    blocked: () => false,
    apply: (snapshotValue) => {
      visibleSnapshot = snapshotValue;
    },
  });

  reloadGet.resolve({ revision: "newer-reload" });
  assert.equal(await reload, true);
  initialGet.resolve({ revision: "stale-initial" });
  descriptorGet.resolve({ connection_types: [{ id: "openai_compatible" }] });
  assert.equal(await initialLoad, false);

  assert.deepEqual(visibleSnapshot, { revision: "newer-reload" });
  assert.deepEqual(installedDescriptors, {
    connection_types: [{ id: "openai_compatible" }],
  });
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
