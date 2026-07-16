import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const modelState = await import("../popup/popup-model-config-state.js").catch(
  () => ({} as Record<string, unknown>),
) as any;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function chatConnection(id: string, overrides: Record<string, unknown> = {}) {
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

function embeddingProvider(id: string, overrides: Record<string, unknown> = {}) {
  const record = chatConnection(id, overrides) as Record<string, unknown>;
  for (const field of ["model", "api_mode", "reasoning_effort", "http_referer", "x_title", "num_ctx"]) {
    delete record[field];
  }
  return record;
}

function snapshot(ids = ["a", "b", "c"], revision = "revision-a") {
  return {
    revision,
    source: "native",
    models: {
      schema_version: 1,
      chat: {
        connections: ids.map((id) => chatConnection(id)),
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
        providers: [embeddingProvider("embedding-a")],
      },
    },
    migration: { state: "none", confirmed: true, issues: [] },
    overrides: [],
  };
}

test("model settings state appends, removes, and reorders stable route peers", () => {
  for (const name of ["hydrateModelConfig", "appendRouteItem", "removeRouteItem", "moveRouteItem"]) {
    assert.equal(typeof modelState[name], "function", `${name} should be exported`);
  }

  let state = modelState.hydrateModelConfig(snapshot(["a", "b"]));
  state = modelState.appendRouteItem(state, "chat", chatConnection("c"));
  assert.deepEqual(state.models.chat.connections.map((item: any) => item.id), ["a", "b", "c"]);
  assert.equal(state.selected.chat, "c");
  state = modelState.moveRouteItem(state, "chat", "c", 0);
  assert.deepEqual(state.models.chat.connections.map((item: any) => item.id), ["c", "a", "b"]);
  assert.equal(state.models.chat.connections[0].credential.action, "keep");
  state = modelState.removeRouteItem(state, "chat", "a");
  assert.deepEqual(state.models.chat.connections.map((item: any) => item.id), ["c", "b"]);
});

test("popup model load installs descriptors independently when its snapshot becomes stale", async () => {
  assert.equal(typeof modelState.loadIndependentModelResources, "function");
  const gate = modelState.createLatestRequestGate();
  const staleSnapshot = deferred<any>();
  const descriptors = deferred<any>();
  const latestSnapshot = deferred<any>();
  let visibleRevision = "";
  let installedTypes: string[] = [];

  const initial = modelState.loadIndependentModelResources({
    gate,
    snapshotRequest: () => staleSnapshot.promise,
    descriptorRequest: () => descriptors.promise,
    blocked: () => false,
    applySnapshot: (value: any) => { visibleRevision = value.revision; },
    installDescriptors: (value: any) => {
      installedTypes = value.connection_types.map((item: any) => item.id);
    },
  });
  const reload = modelState.applyLatestSnapshotRequest({
    gate,
    request: () => latestSnapshot.promise,
    blocked: () => false,
    apply: (value: any) => { visibleRevision = value.revision; },
  });

  latestSnapshot.resolve({ revision: "revision-new" });
  assert.equal(await reload, true);
  staleSnapshot.reject(new Error("stale initial snapshot failed"));
  descriptors.resolve({ connection_types: [{ id: "ollama" }], groups: [] });

  assert.deepEqual(await initial, {
    snapshotApplied: false,
    descriptorsInstalled: true,
  });
  assert.equal(visibleRevision, "revision-new");
  assert.deepEqual(installedTypes, ["ollama"]);
});

test("pending popup model load retains a new dirty draft and reports the snapshot as remote", async () => {
  const snapshotGate = modelState.createLatestRequestGate();
  const descriptorGate = modelState.createLatestRequestGate();
  const pendingSnapshot = deferred<any>();
  let current = modelState.hydrateModelConfig(snapshot(["a"], "revision-a"));

  const loading = modelState.loadIndependentModelResources({
    gate: snapshotGate,
    descriptorGate,
    snapshotRequest: () => pendingSnapshot.promise,
    descriptorRequest: async () => ({ connection_types: [], groups: [] }),
    blocked: () => Boolean(current.dirty),
    onSnapshotBlocked: (value: any) => {
      current = modelState.receiveRemoteSnapshot(current, value, { force: true });
    },
    applySnapshot: (value: any) => {
      current = modelState.hydrateModelConfig(value);
    },
    installDescriptors: () => {},
  });

  current = modelState.updateRouteField(
    current,
    "chat",
    "a",
    "model",
    "local-edit-while-loading",
  );
  pendingSnapshot.resolve(snapshot(["remote"], "revision-a"));

  assert.deepEqual(await loading, {
    snapshotApplied: false,
    descriptorsInstalled: true,
  });
  assert.equal(current.models.chat.connections[0].model, "local-edit-while-loading");
  assert.notEqual(current.remoteUpdate, null);
  assert.equal(current.remoteUpdate.latestRevision, "revision-a");
  assert.equal(current.remoteUpdate.snapshot.models.chat.connections[0].id, "remote");
});

test("full load revalidates snapshot ownership after its snapshot already applied", async () => {
  const snapshotGate = modelState.createLatestRequestGate();
  const descriptorGate = modelState.createLatestRequestGate();
  const firstSnapshot = deferred<any>();
  const firstSnapshotApplied = deferred<void>();
  const firstDescriptors = deferred<any>();
  const reloadSnapshot = deferred<any>();
  let current = modelState.hydrateModelConfig(snapshot(["initial"], "revision-initial"));
  let puts = 0;

  const fullLoad = modelState.loadIndependentModelResources({
    gate: snapshotGate,
    descriptorGate,
    snapshotRequest: () => firstSnapshot.promise,
    descriptorRequest: () => firstDescriptors.promise,
    blocked: () => false,
    applySnapshot: (value: any) => {
      current = modelState.hydrateModelConfig(value);
      firstSnapshotApplied.resolve();
    },
    installDescriptors: () => {},
  });
  firstSnapshot.resolve(snapshot(["load-a"], "revision-a"));
  await firstSnapshotApplied.promise;

  const reload = modelState.applyLatestSnapshotRequest({
    gate: snapshotGate,
    request: () => reloadSnapshot.promise,
    blocked: () => false,
    apply: (value: any) => {
      current = modelState.hydrateModelConfig(value);
    },
  });
  firstDescriptors.resolve({ connection_types: [{ id: "ollama" }], groups: [] });

  const loaded = await fullLoad;
  const operations = modelState.createModelOperationGate();
  if (operations.canStartSaveAfterLoad({
    startedSaveGeneration: operations.saveGeneration,
    loadResult: loaded,
    state: current,
  })) {
    puts += 1;
    snapshotGate.invalidate();
  }
  reloadSnapshot.resolve(snapshot(["reload-b"], "revision-b"));

  assert.deepEqual(loaded, {
    snapshotApplied: false,
    descriptorsInstalled: true,
  });
  assert.equal(puts, 0, "the superseded full load must not issue a convenience PUT");
  assert.equal(await reload, true, "the still-current snapshot-only reload must not be invalidated");
  assert.equal(current.revision, "revision-b");
});

test("full load revalidates descriptor ownership after its descriptor already applied", async () => {
  const snapshotGate = modelState.createLatestRequestGate();
  const descriptorGate = modelState.createLatestRequestGate();
  const firstSnapshot = deferred<any>();
  const firstDescriptors = deferred<any>();
  const firstDescriptorsApplied = deferred<void>();
  const replacementDescriptors = deferred<any>();
  let installedTypes: string[] = [];

  const fullLoad = modelState.loadIndependentModelResources({
    gate: snapshotGate,
    descriptorGate,
    snapshotRequest: () => firstSnapshot.promise,
    descriptorRequest: () => firstDescriptors.promise,
    blocked: () => false,
    applySnapshot: () => {},
    installDescriptors: (value: any) => {
      installedTypes = value.connection_types.map((item: any) => item.id);
      firstDescriptorsApplied.resolve();
    },
  });
  firstDescriptors.resolve({ connection_types: [{ id: "descriptor-a" }], groups: [] });
  await firstDescriptorsApplied.promise;

  const descriptorReload = modelState.applyLatestSnapshotRequest({
    gate: descriptorGate,
    request: () => replacementDescriptors.promise,
    blocked: () => false,
    apply: (value: any) => {
      installedTypes = value.connection_types.map((item: any) => item.id);
    },
  });
  firstSnapshot.resolve(snapshot(["load-a"], "revision-a"));

  assert.deepEqual(await fullLoad, {
    snapshotApplied: true,
    descriptorsInstalled: false,
  });
  replacementDescriptors.resolve({
    connection_types: [{ id: "descriptor-b" }],
    groups: [],
  });
  assert.equal(await descriptorReload, true);
  assert.deepEqual(installedTypes, ["descriptor-b"]);
});

test("full resource failure waits for a winning sibling before owning final status", async () => {
  const snapshotGate = modelState.createLatestRequestGate();
  const descriptorGate = modelState.createLatestRequestGate();
  const failedSnapshot = deferred<any>();
  const delayedDescriptors = deferred<any>();
  const expectedError = new Error("current snapshot failed");
  let completed = false;
  let blocked = false;
  let descriptorsInstalled = false;
  let visibleStatus = "loading";

  const completion = modelState.loadIndependentModelResources({
    gate: snapshotGate,
    descriptorGate,
    snapshotRequest: () => failedSnapshot.promise,
    descriptorRequest: () => delayedDescriptors.promise,
    blocked: () => blocked,
    applySnapshot: () => {},
    installDescriptors: () => {
      descriptorsInstalled = true;
      visibleStatus = "synced by descriptor render";
    },
  }).then(
    (value: any) => {
      completed = true;
      visibleStatus = "loaded";
      return { value, error: null };
    },
    (error: Error) => {
      completed = true;
      visibleStatus = `error: ${error.message}`;
      return { value: null, error };
    },
  );

  failedSnapshot.reject(expectedError);
  await new Promise<void>((resolvePromise) => setImmediate(resolvePromise));
  const failedFast = completed;
  blocked = true;
  delayedDescriptors.resolve({ connection_types: [{ id: "ollama" }], groups: [] });
  const outcome = await completion;

  assert.equal(failedFast, false, "a current failure must wait for its sibling to settle");
  assert.equal(descriptorsInstalled, true, "the winning sibling still applies independently");
  assert.equal(outcome.error, expectedError);
  assert.equal(visibleStatus, "error: current snapshot failed");
});

test("overlapping popup model loads keep the latest descriptor registry", async () => {
  const snapshotGate = modelState.createLatestRequestGate();
  const descriptorGate = modelState.createLatestRequestGate();
  const oldSnapshot = deferred<any>();
  const oldDescriptors = deferred<any>();
  const newSnapshot = deferred<any>();
  const newDescriptors = deferred<any>();
  let visibleRevision = "";
  let installedTypes: string[] = [];

  const startLoad = (snapshotRequest: () => Promise<any>, descriptorRequest: () => Promise<any>) => (
    modelState.loadIndependentModelResources({
      gate: snapshotGate,
      descriptorGate,
      snapshotRequest,
      descriptorRequest,
      blocked: () => false,
      applySnapshot: (value: any) => { visibleRevision = value.revision; },
      installDescriptors: (value: any) => {
        installedTypes = value.connection_types.map((item: any) => item.id);
      },
    })
  );

  const older = startLoad(() => oldSnapshot.promise, () => oldDescriptors.promise);
  const newer = startLoad(() => newSnapshot.promise, () => newDescriptors.promise);
  newDescriptors.resolve({ connection_types: [{ id: "new-type" }], groups: [] });
  newSnapshot.resolve({ revision: "revision-new" });
  assert.deepEqual(await newer, { snapshotApplied: true, descriptorsInstalled: true });

  oldDescriptors.resolve({ connection_types: [{ id: "old-type" }], groups: [] });
  oldSnapshot.resolve({ revision: "revision-old" });
  assert.deepEqual(await older, { snapshotApplied: false, descriptorsInstalled: false });
  assert.equal(visibleRevision, "revision-new");
  assert.deepEqual(installedTypes, ["new-type"]);
});

test("stale popup descriptor rejection cannot clobber a newer completed load", async () => {
  const snapshotGate = modelState.createLatestRequestGate();
  const descriptorGate = modelState.createLatestRequestGate();
  const oldSnapshot = deferred<any>();
  const oldDescriptors = deferred<any>();
  const newSnapshot = deferred<any>();
  const newDescriptors = deferred<any>();
  let installedTypes: string[] = [];
  const options = (snapshotRequest: () => Promise<any>, descriptorRequest: () => Promise<any>) => ({
    gate: snapshotGate,
    descriptorGate,
    snapshotRequest,
    descriptorRequest,
    blocked: () => false,
    applySnapshot: () => {},
    installDescriptors: (value: any) => {
      installedTypes = value.connection_types.map((item: any) => item.id);
    },
  });

  const older = modelState.loadIndependentModelResources(
    options(() => oldSnapshot.promise, () => oldDescriptors.promise),
  );
  const newer = modelState.loadIndependentModelResources(
    options(() => newSnapshot.promise, () => newDescriptors.promise),
  );
  newSnapshot.resolve({ revision: "revision-new" });
  newDescriptors.resolve({ connection_types: [{ id: "new-type" }], groups: [] });
  assert.deepEqual(await newer, { snapshotApplied: true, descriptorsInstalled: true });

  oldSnapshot.resolve({ revision: "revision-old" });
  oldDescriptors.reject(new Error("stale descriptor request failed"));
  assert.deepEqual(await older, { snapshotApplied: false, descriptorsInstalled: false });
  assert.deepEqual(installedTypes, ["new-type"]);
});

test("one-click load guard refuses a PUT after an edit, save, or stale snapshot", async (t) => {
  assert.equal(typeof modelState.createModelOperationGate, "function");

  await t.test("edit while the load is pending", async () => {
    const operations = modelState.createModelOperationGate();
    const load = deferred<any>();
    let current = modelState.hydrateModelConfig(snapshot(["a"]));
    let puts = 0;
    const startedSaveGeneration = operations.saveGeneration;
    const oneClick = (async () => {
      const loaded = await load.promise;
      if (operations.canStartSaveAfterLoad({
        startedSaveGeneration,
        loadResult: loaded,
        state: current,
      })) puts += 1;
    })();

    current = modelState.updateRouteField(current, "chat", "a", "model", "local-edit");
    load.resolve({ snapshotApplied: true, descriptorsInstalled: true });
    await oneClick;
    assert.equal(puts, 0);
    assert.equal(current.models.chat.connections[0].model, "local-edit");
  });

  await t.test("normal save begins while the load is pending", async () => {
    const operations = modelState.createModelOperationGate();
    const load = deferred<any>();
    const current = modelState.hydrateModelConfig(snapshot(["a"]));
    let puts = 0;
    const stateBefore = structuredClone(current);
    const startedSaveGeneration = operations.saveGeneration;
    const oneClick = (async () => {
      const loaded = await load.promise;
      if (operations.canStartSaveAfterLoad({
        startedSaveGeneration,
        loadResult: loaded,
        state: current,
      })) puts += 1;
    })();

    const normalSave = operations.beginSave();
    assert.notEqual(normalSave, null);
    puts += 1;
    load.resolve({ snapshotApplied: true, descriptorsInstalled: true });
    await oneClick;
    assert.equal(puts, 1, "the normal save is the only PUT");
    assert.deepEqual(current, stateBefore);
    assert.equal(operations.finishSave(normalSave.generation), true);
  });

  await t.test("snapshot was superseded", async () => {
    const operations = modelState.createModelOperationGate();
    const current = modelState.hydrateModelConfig(snapshot(["a"]));
    assert.equal(operations.canStartSaveAfterLoad({
      startedSaveGeneration: operations.saveGeneration,
      loadResult: { snapshotApplied: false, descriptorsInstalled: true },
      state: current,
    }), false);
  });
});

test("generic save failure clears an invalidated probe label and unlocks its button", async () => {
  assert.equal(typeof modelState.createModelOperationGate, "function");
  const operations = modelState.createModelOperationGate();
  const pendingProbe = deferred<{ ok: boolean }>();
  const pendingSave = deferred<void>();
  let attached = false;
  let visibleProbeStatus = "probing";

  const probeGeneration = operations.beginProbe();
  const probeTask = (async () => {
    await pendingProbe.promise;
    if (operations.isProbeCurrent(probeGeneration)) {
      attached = true;
      visibleProbeStatus = "passed";
    }
    operations.finishProbe(probeGeneration);
  })();
  const save = operations.beginSave();
  assert.notEqual(save, null);
  assert.equal(save.invalidatedProbe, true);
  if (save.invalidatedProbe) visibleProbeStatus = "not yet probed";
  const saveTask = (async () => {
    try {
      await pendingSave.promise;
    } catch {
      // A generic save error updates the save status, not the accepted probe state.
    } finally {
      operations.finishSave(save.generation);
    }
  })();

  pendingProbe.resolve({ ok: true });
  await probeTask;
  assert.equal(attached, false);
  assert.deepEqual(operations.controlState(), {
    editorLocked: true,
    saveDisabled: true,
    probeDisabled: true,
  });

  pendingSave.reject(new Error("generic save failure"));
  await saveTask;
  assert.equal(visibleProbeStatus, "not yet probed");
  assert.deepEqual(operations.controlState(), {
    editorLocked: false,
    saveDisabled: false,
    probeDisabled: false,
  });
});

test("model settings state enforces ten peers and protects the final active route item", () => {
  const ten = modelState.hydrateModelConfig(
    snapshot(Array.from({ length: 10 }, (_, index) => `chat-${index}`)),
  );
  assert.throws(
    () => modelState.appendRouteItem(ten, "chat", chatConnection("overflow")),
    /maximum 10/i,
  );
  const oneChat = modelState.hydrateModelConfig(snapshot(["only"]));
  assert.throws(() => modelState.removeRouteItem(oneChat, "chat", "only"), /at least one/i);

  const oneEmbedding = modelState.hydrateModelConfig(snapshot());
  assert.throws(
    () => modelState.removeRouteItem(oneEmbedding, "embedding", "embedding-a"),
    /at least one/i,
  );
});

test("model settings presets fill untouched defaults without overwriting custom values", () => {
  let state = modelState.hydrateModelConfig(snapshot(["a"]));
  state = modelState.updateRouteField(
    state,
    "chat",
    "a",
    "base_url",
    "https://custom.test/v1",
  );
  state = modelState.applyPreset(
    state,
    "chat",
    "a",
    {
      id: "openrouter",
      defaults: {
        base_url: "https://openrouter.ai/api/v1",
        api_mode: "responses",
        http_referer: "https://openbiliclaw.local",
      },
    },
    { previousPreset: { id: "custom", defaults: { api_mode: "chat_completions" } } },
  );

  const record = state.models.chat.connections[0];
  assert.equal(record.preset, "openrouter");
  assert.equal(record.base_url, "https://custom.test/v1");
  assert.equal(record.api_mode, "responses");
  assert.equal(record.http_referer, "https://openbiliclaw.local");
});

test("model settings credentials are explicit keep, set, clear, or env actions", () => {
  let state = modelState.hydrateModelConfig(snapshot(["a"]));
  assert.equal(state.models.chat.connections[0].credential.action, "keep");
  assert.equal(state.models.chat.connections[0].credential.value, "");

  for (const [action, value, expected] of [
    ["set", "new-secret", { action: "set", value: "new-secret" }],
    ["env", "OPENBILICLAW_API_KEY", { action: "env", value: "OPENBILICLAW_API_KEY" }],
    ["clear", "ignored", { action: "clear" }],
    ["keep", "ignored", { action: "keep" }],
  ] as const) {
    state = modelState.updateRouteField(state, "chat", "a", "credential", { action, value });
    const payload = modelState.toModelConfigPayload(state);
    assert.deepEqual(payload.models.chat.connections[0].credential, expected);
  }
});

test("model settings payload is revisioned, strips health, and keeps embedding model shared", () => {
  const source = snapshot(["a"]);
  (source.models.chat.connections[0].credential as any).value = "must-not-survive";
  const state = modelState.hydrateModelConfig(source);
  const payload = modelState.toModelConfigPayload(state);

  assert.equal(payload.revision, "revision-a");
  assert.equal("probe" in payload.models.chat.connections[0], false);
  assert.equal("circuit" in payload.models.chat.connections[0], false);
  assert.equal("model" in payload.models.embedding.providers[0], false);
  assert.equal(payload.models.embedding.settings.model, "bge-m3");
  assert.equal(JSON.stringify(state).includes("must-not-survive"), false);
});

test("model settings exact probe results require revision, ID, draft, and shared embedding settings", () => {
  let state = modelState.hydrateModelConfig(snapshot(["a", "b"]));
  const signature = modelState.createProbeSignature(state, "chat", "a");
  state = modelState.moveRouteItem(state, "chat", "a", 1);
  state = modelState.selectRouteItem(state, "chat", "b");
  const accepted = modelState.applyProbeResult(state, signature, { ok: true, connection_id: "a" });
  assert.equal(accepted.accepted, true);
  assert.equal(
    accepted.state.models.chat.connections.find((item: any) => item.id === "a").probe.ok,
    true,
  );
  assert.equal(
    accepted.state.models.chat.connections.find((item: any) => item.id === "b").probe,
    null,
  );

  const edited = modelState.updateRouteField(state, "chat", "a", "model", "other-model");
  assert.equal(modelState.applyProbeResult(edited, signature, { ok: true }).accepted, false);
  const revised = modelState.hydrateModelConfig(snapshot(["a", "b"], "revision-b"));
  assert.equal(modelState.applyProbeResult(revised, signature, { ok: true }).accepted, false);

  const embedding = modelState.hydrateModelConfig(snapshot());
  const embeddingSignature = modelState.createProbeSignature(embedding, "embedding", "embedding-a");
  const changedSettings = modelState.updateRouteSetting(
    embedding,
    "embedding",
    "model",
    "different-vector-space",
  );
  assert.equal(
    modelState.applyProbeResult(changedSettings, embeddingSignature, { ok: true }).accepted,
    false,
  );
});

test("model settings retain dirty drafts on remote revision and resolve migrations without positions", () => {
  let state = modelState.hydrateModelConfig(snapshot(["a"], "revision-a"));
  state = modelState.updateRouteField(state, "chat", "a", "model", "local-model");
  const retained = modelState.receiveRemoteSnapshot(state, snapshot(["remote"], "revision-b"));
  assert.equal(retained.models.chat.connections[0].model, "local-model");
  assert.equal(retained.remoteUpdate.latestRevision, "revision-b");

  state = modelState.setMigrationResolution(retained, "legacy-1", {
    action: "add_to_chat_route",
    position: 9,
  });
  assert.deepEqual(modelState.toModelConfigPayload(state).migration_resolutions, {
    "legacy-1": { action: "add_to_chat_route" },
  });
});

test("model settings derive local override locks including embedding enabled from provider lock", () => {
  const source = snapshot();
  source.overrides = [
    { path: "models.chat.connections", source: "config.local.toml" },
    { path: "models.embedding.providers", source: "config.local.toml" },
  ];
  const state = modelState.hydrateModelConfig(source);

  assert.equal(state.overrideLocks["models.chat.connections"].source, "config.local.toml");
  assert.equal(state.overrideLocks["models.embedding.providers"].source, "config.local.toml");
  assert.equal(state.overrideLocks["models.embedding.enabled"].source, "config.local.toml");
  assert.equal(state.overrideLocks["models.embedding.settings.model"], null);
});

test("one-click local Ollama prepares one shared embedding route and preserves Chat credentials", () => {
  assert.equal(typeof modelState.prepareLocalOllamaEmbedding, "function");
  const source = snapshot(["chat-primary", "chat-fallback"]);
  source.models.embedding.enabled = false;
  source.models.embedding.providers = [];
  const before = modelState.hydrateModelConfig(source);
  const descriptor = {
    id: "ollama",
    label: "Ollama",
    category: "local_runtime",
    capabilities: ["chat", "embedding"],
    fields: [
      { name: "model", capabilities: ["chat"] },
      { name: "base_url", capabilities: [] },
      { name: "num_ctx", capabilities: ["chat"] },
    ],
    preset_definitions: [],
  };

  const next = modelState.prepareLocalOllamaEmbedding(before, descriptor, {
    id: "embedding-local-ollama",
    name: "Local Ollama",
    model: "bge-m3",
    output_dimensionality: 1024,
    base_url: "http://127.0.0.1:11434/v1",
  });
  const payload = modelState.toModelConfigPayload(next);

  assert.equal(payload.revision, "revision-a");
  assert.deepEqual(
    payload.models.chat.connections.map((item: any) => item.id),
    ["chat-primary", "chat-fallback"],
  );
  assert.deepEqual(
    payload.models.chat.connections.map((item: any) => item.credential),
    [{ action: "keep" }, { action: "keep" }],
  );
  assert.deepEqual(payload.models.embedding.settings, {
    model: "bge-m3",
    output_dimensionality: 1024,
    similarity_threshold: 0.82,
    multimodal_enabled: false,
  });
  assert.deepEqual(payload.models.embedding.providers, [{
    id: "embedding-local-ollama",
    name: "Local Ollama",
    type: "ollama",
    preset: "",
    base_url: "http://127.0.0.1:11434/v1",
    credential: { action: "clear" },
  }]);
  assert.equal("model" in payload.models.embedding.providers[0], false);
});

test("one-click local Ollama refuses dirty, overridden, or configured non-Ollama routes", () => {
  const descriptor = {
    id: "ollama",
    category: "local_runtime",
    capabilities: ["embedding"],
    fields: [{ name: "base_url", capabilities: ["embedding"] }],
  };
  const defaults = {
    id: "embedding-local-ollama",
    name: "Local Ollama",
    model: "bge-m3",
    output_dimensionality: 1024,
    base_url: "http://127.0.0.1:11434/v1",
  };

  const configured = modelState.hydrateModelConfig(snapshot());
  assert.throws(
    () => modelState.prepareLocalOllamaEmbedding(configured, descriptor, defaults),
    /configured embedding route/i,
  );
  assert.equal(configured.models.embedding.providers[0].credential.action, "keep");

  const emptySource = snapshot();
  emptySource.models.embedding.enabled = false;
  emptySource.models.embedding.providers = [];
  let dirty = modelState.hydrateModelConfig(emptySource);
  dirty = modelState.updateRouteSetting(dirty, "embedding", "model", "local-change");
  assert.throws(
    () => modelState.prepareLocalOllamaEmbedding(dirty, descriptor, defaults),
    /unsaved model changes/i,
  );

  const overriddenSource = structuredClone(emptySource);
  overriddenSource.overrides = [
    { path: "models.embedding.providers", source: "config.local.toml" },
  ];
  const overridden = modelState.hydrateModelConfig(overriddenSource);
  assert.throws(
    () => modelState.prepareLocalOllamaEmbedding(overridden, descriptor, defaults),
    /read-only override/i,
  );
});

test("model settings page is sequential list/detail and removes every legacy provider form", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const controller = readFileSync(resolve("popup", "popup-model-settings.js"), "utf8");

  for (const id of [
    "popupModelRouteTabs",
    "popupModelRouteList",
    "popupModelDetail",
    "popupModelDetailBack",
    "popupModelTypeSearch",
    "popupModelConnectionTypeGroups",
    "popupModelEmbeddingSharedSettings",
    "popupModelInspectorFields",
    "popupModelSaveButton",
    "popupModelProbeButton",
    "popupModelReloadRemote",
    "popupModelMigrationPanel",
  ]) {
    assert.match(popupHtml, new RegExp(`id="${id}"`), `${id} should exist`);
    assert.match(controller, new RegExp(`"${id}"`), `${id} should be wired`);
  }

  for (const legacyId of [
    "cfgLlmProvider",
    "cfgLlmFallbackProvider",
    "cfgEmbeddingProvider",
    "cfgEmbeddingFallbackProvider",
    "cfgOpenaiAuthMode",
    "cfgOpenaiKey",
    "cfgClaudeKey",
    "cfgGeminiKey",
    "cfgDeepseekKey",
    "cfgOllamaModel",
    "cfgOpenrouterKey",
    "cfgOpenaiCompatibleKey",
    "cfgModuleSoulProvider",
    "cfgModuleDiscoveryProvider",
    "cfgModuleRecommendationProvider",
    "cfgModuleEvaluationProvider",
  ]) {
    assert.doesNotMatch(popupHtml, new RegExp(`id="${legacyId}"`));
    assert.doesNotMatch(popupJs, new RegExp(`"${legacyId}"`));
  }

  assert.match(popupHtml, /data-popup-model-route="chat"/);
  assert.match(popupHtml, /data-popup-model-route="embedding"/);
  assert.match(popupHtml, /data-popup-model-route="runtime"/);
  assert.match(popupHtml, /popup-model-route-layout/);
  assert.match(popupHtml, /popup-model-route-page/);
  assert.match(popupHtml, /popup-model-detail-page/);
  assert.match(controller, /fetchModelConnectionTypes/);
  assert.match(controller, /descriptor\.fields/);
  assert.match(controller, /createProbeSignature/);
  assert.match(controller, /saveInFlight/);
  assert.match(controller, /receiveRemoteSnapshot/);
  assert.match(controller, /focusSelectedRouteControl/);
});

test("model and general settings saves are separate scopes", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
  const controller = readFileSync(resolve("popup", "popup-model-settings.js"), "utf8");

  assert.match(popupHtml, /id="popupModelSaveButton"[^>]*>保存模型 route</);
  assert.match(popupHtml, /id="settingsSave"[^>]*>保存通用配置</);
  assert.match(controller, /updateModelConfig\(toModelConfigPayload\(state\)\)/);
  assert.match(popupJs, /updateConfig\(data\)/);
  assert.doesNotMatch(popupJs, /\bllm:\s*\{/);
  assert.doesNotMatch(popupJs, /cfg\.llm\?\./);
  assert.doesNotMatch(popupJs, /probeConfigService\("(?:llm|llm_fallback|embedding)"/);
});

test("model controller handles revision conflict, save locking, and popup focus restoration", () => {
  const controller = readFileSync(resolve("popup", "popup-model-settings.js"), "utf8");

  assert.match(controller, /error\.status === 409/);
  assert.match(controller, /error\.details\?\.error === "revision_conflict"/);
  assert.match(controller, /setModelEditorLocked\(true\)/);
  assert.match(controller, /setModelEditorLocked\(false\)/);
  assert.match(controller, /if \(!state \|\| modelOperations\.saveInFlight\) return/);
  assert.match(controller, /beforeunload/);
  assert.match(controller, /config_reloaded/);
  assert.match(controller, /requestAnimationFrame/);
  assert.match(controller, /popupModelDetailBack/);
  assert.match(controller, /prepareLocalOllamaEmbedding/);
  assert.match(controller, /updateModelConfig\(toModelConfigPayload\(prepared\)\)/);
  assert.match(controller, /configured embedding route/);
  assert.doesNotMatch(controller, /updateConfig\([^)]*llm/);
});

test("model controller owns load, descriptor, probe, and save completion races", () => {
  const controller = readFileSync(resolve("popup", "popup-model-settings.js"), "utf8");

  assert.match(controller, /createModelOperationGate/);
  assert.match(controller, /const modelOperations = createModelOperationGate\(\)/);
  assert.match(controller, /const descriptorRequestGate = createLatestRequestGate\(\)/);
  assert.match(controller, /descriptorGate: descriptorRequestGate/);
  assert.match(
    controller,
    /blocked: \(\) => modelOperations\.saveInFlight \|\| Boolean\(state\?\.dirty\)/,
  );
  assert.match(controller, /onSnapshotBlocked:/);
  assert.match(controller, /canStartSaveAfterLoad/);
  assert.match(controller, /modelOperations\.beginProbe\(\)/);
  assert.match(controller, /function beginModelSave\(\)/);
  assert.match(controller, /const save = modelOperations\.beginSave\(\)/);
  assert.match(controller, /save\?\.invalidatedProbe/);
  assert.match(controller, /renderProbeStatus\(selectedRecord\(state, state\.activeRoute\)\)/);
  assert.equal(controller.match(/const save = beginModelSave\(\)/g)?.length, 2);
  assert.match(controller, /modelOperations\.controlState\(\)/);
});
