import test from "node:test";
import assert from "node:assert/strict";

import { createDialogFocusController } from "../../src/openbiliclaw/web/js/saved-sync-runtime.js";

let controller = {};
let controllerLoadError = null;
try {
  controller = await import(
    "../../src/openbiliclaw/web/js/mobile-model-settings-controller.js"
  );
} catch (error) {
  controllerLoadError = error;
}

function requiredExport(name) {
  assert.equal(
    typeof controller[name],
    "function",
    `${name} must be a production controller export${
      controllerLoadError ? ` (${controllerLoadError.message})` : ""
    }`,
  );
  return controller[name];
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

function numericState(overrides = {}) {
  return {
    models: {
      chat: {
        concurrency: 4,
        timeout_seconds: 300,
        connections: [
          { id: "chat-primary", num_ctx: 0 },
          { id: "chat-fallback", num_ctx: 4096 },
        ],
      },
      embedding: {
        settings: {
          output_dimensionality: 1024,
          similarity_threshold: 0.82,
        },
      },
    },
    ...overrides,
  };
}

function loadRecoveryHarness(createRecovery) {
  const visible = {
    locked: true,
    retry: false,
    status: "",
    tone: "",
  };
  const recovery = createRecovery({
    setLocked(value) { visible.locked = value; },
    setRetryVisible(value) { visible.retry = value; },
    onLoading() {
      visible.status = "loading";
      visible.tone = "";
    },
    onReady() {
      visible.status = "synchronized";
      visible.tone = "";
    },
    onRecoverableIncomplete() {
      visible.status = "incomplete-retry";
      visible.tone = "error";
    },
    onError(error) {
      visible.status = error.message;
      visible.tone = "error";
    },
  });
  return { visible, recovery };
}

test("Saved Sync reload before first Models still loads descriptors and unlocks the editor", async () => {
  const createCoordinator = requiredExport("createMobileModelResourceCoordinator");
  let snapshotRequests = 0;
  let descriptorRequests = 0;
  let visibleSnapshot = null;
  let installedDescriptors = null;
  let editorLocked = true;
  const coordinator = createCoordinator({
    snapshotRequest: async () => {
      snapshotRequests += 1;
      return { revision: "reload-before-models" };
    },
    descriptorRequest: async () => {
      descriptorRequests += 1;
      return { connection_types: [{ id: "openai_compatible" }] };
    },
    blocked: () => false,
    applySnapshot(snapshot) { visibleSnapshot = snapshot; },
    installDescriptors(descriptors) { installedDescriptors = descriptors; },
    onReadinessChange(readiness) { editorLocked = !readiness.ready; },
  });

  await coordinator.reloadSnapshot();
  assert.equal(visibleSnapshot.revision, "reload-before-models");
  assert.deepEqual(coordinator.readiness(), {
    snapshotReady: true,
    descriptorsReady: false,
    ready: false,
    loading: false,
  });

  await coordinator.enterModels();
  assert.equal(snapshotRequests, 1, "entering Models must reuse the current snapshot");
  assert.equal(descriptorRequests, 1);
  assert.equal(installedDescriptors.connection_types[0].id, "openai_compatible");
  assert.equal(editorLocked, false);
  assert.equal(coordinator.readiness().ready, true);
});

test("descriptor failure remains retryable after snapshot success", async () => {
  const createCoordinator = requiredExport("createMobileModelResourceCoordinator");
  let snapshotRequests = 0;
  let descriptorRequests = 0;
  let installed = false;
  const coordinator = createCoordinator({
    snapshotRequest: async () => {
      snapshotRequests += 1;
      return { revision: "snapshot-ready" };
    },
    descriptorRequest: async () => {
      descriptorRequests += 1;
      if (descriptorRequests === 1) throw new Error("descriptor registry unavailable");
      return { connection_types: [{ id: "ollama" }] };
    },
    blocked: () => false,
    applySnapshot() {},
    installDescriptors() { installed = true; },
  });

  await assert.rejects(coordinator.enterModels(), /descriptor registry unavailable/);
  assert.equal(coordinator.readiness().snapshotReady, true);
  assert.equal(coordinator.readiness().descriptorsReady, false);
  assert.equal(coordinator.readiness().ready, false);

  await coordinator.enterModels();
  assert.equal(snapshotRequests, 1, "descriptor retry must not discard/refetch the snapshot");
  assert.equal(descriptorRequests, 2);
  assert.equal(installed, true);
  assert.equal(coordinator.readiness().ready, true);
});

test("newer reload wins over an in-flight full load while current descriptors install", async () => {
  const createCoordinator = requiredExport("createMobileModelResourceCoordinator");
  const oldSnapshot = deferred();
  const newSnapshot = deferred();
  const descriptor = deferred();
  let snapshotRequests = 0;
  let visibleRevision = "";
  let descriptorId = "";
  const coordinator = createCoordinator({
    snapshotRequest: () => {
      snapshotRequests += 1;
      return snapshotRequests === 1 ? oldSnapshot.promise : newSnapshot.promise;
    },
    descriptorRequest: () => descriptor.promise,
    blocked: () => false,
    applySnapshot(snapshot) { visibleRevision = snapshot.revision; },
    installDescriptors(value) { descriptorId = value.connection_types[0].id; },
  });

  const fullLoad = coordinator.enterModels();
  const reload = coordinator.reloadSnapshot();
  newSnapshot.resolve({ revision: "newest" });
  await reload;
  oldSnapshot.resolve({ revision: "stale" });
  descriptor.resolve({ connection_types: [{ id: "current-descriptor" }] });
  await fullLoad;

  assert.equal(visibleRevision, "newest");
  assert.equal(descriptorId, "current-descriptor");
  assert.equal(coordinator.readiness().ready, true);
});

test("readiness stays locked until both independently loaded resources are ready", async () => {
  const createCoordinator = requiredExport("createMobileModelResourceCoordinator");
  const snapshot = deferred();
  const descriptor = deferred();
  const transitions = [];
  const coordinator = createCoordinator({
    snapshotRequest: () => snapshot.promise,
    descriptorRequest: () => descriptor.promise,
    blocked: () => false,
    applySnapshot() {},
    installDescriptors() {},
    onReadinessChange(readiness) { transitions.push({ ...readiness }); },
  });

  const load = coordinator.enterModels();
  assert.equal(coordinator.readiness().loading, true);
  assert.equal(coordinator.readiness().ready, false);
  snapshot.resolve({ revision: "ready" });
  await Promise.resolve();
  assert.equal(coordinator.readiness().ready, false);
  descriptor.resolve({ connection_types: [] });
  await load;

  assert.equal(coordinator.readiness().ready, true);
  assert.ok(transitions.some((value) => value.loading && !value.ready));
  assert.deepEqual(transitions.at(-1), {
    snapshotReady: true,
    descriptorsReady: true,
    ready: true,
    loading: false,
  });
});

test("a rejected winning reload leaves stale full-load completion visibly retryable", async () => {
  const createCoordinator = requiredExport("createMobileModelResourceCoordinator");
  const createRecovery = requiredExport("createMobileModelLoadRecoveryController");
  const firstSnapshot = deferred();
  const winningReload = deferred();
  const retrySnapshot = deferred();
  const descriptors = deferred();
  const snapshots = [firstSnapshot, winningReload, retrySnapshot];
  let requestIndex = 0;
  let visibleRevision = "";
  const { visible, recovery } = loadRecoveryHarness(createRecovery);
  const coordinator = createCoordinator({
    snapshotRequest: () => snapshots[requestIndex++].promise,
    descriptorRequest: () => descriptors.promise,
    blocked: () => false,
    applySnapshot(snapshot) { visibleRevision = snapshot.revision; },
    installDescriptors() {},
    onReadinessChange: recovery.onReadinessChange,
  });

  recovery.beginEntry();
  const fullLoad = coordinator.enterModels();
  const reloadFailure = coordinator.reloadSnapshot().catch((error) => error);
  winningReload.reject(new Error("reload B failed"));
  assert.match((await reloadFailure).message, /reload B failed/);
  firstSnapshot.resolve({ revision: "stale-A" });
  descriptors.resolve({ connection_types: [] });
  const incomplete = await fullLoad;
  recovery.settleEntry(incomplete);

  assert.deepEqual(incomplete, {
    snapshotReady: false,
    descriptorsReady: true,
    ready: false,
    loading: false,
  });
  assert.equal(visibleRevision, "", "stale A must never become visible");
  assert.deepEqual(visible, {
    locked: true,
    retry: true,
    status: "incomplete-retry",
    tone: "error",
  });

  recovery.beginEntry();
  const retry = coordinator.enterModels();
  retrySnapshot.resolve({ revision: "retry-C" });
  recovery.settleEntry(await retry);
  assert.equal(visibleRevision, "retry-C");
  assert.deepEqual(visible, {
    locked: false,
    retry: false,
    status: "synchronized",
    tone: "",
  });
});

test("a late winning reload clears an interim retry state and unlocks only when ready", async () => {
  const createCoordinator = requiredExport("createMobileModelResourceCoordinator");
  const createRecovery = requiredExport("createMobileModelLoadRecoveryController");
  const firstSnapshot = deferred();
  const winningReload = deferred();
  const descriptors = deferred();
  let snapshotRequests = 0;
  let visibleRevision = "";
  const { visible, recovery } = loadRecoveryHarness(createRecovery);
  const coordinator = createCoordinator({
    snapshotRequest: () => {
      snapshotRequests += 1;
      return snapshotRequests === 1 ? firstSnapshot.promise : winningReload.promise;
    },
    descriptorRequest: () => descriptors.promise,
    blocked: () => false,
    applySnapshot(snapshot) { visibleRevision = snapshot.revision; },
    installDescriptors() {},
    onReadinessChange: recovery.onReadinessChange,
  });

  recovery.beginEntry();
  const fullLoad = coordinator.enterModels();
  const reload = coordinator.reloadSnapshot();
  firstSnapshot.resolve({ revision: "stale-A" });
  descriptors.resolve({ connection_types: [] });
  recovery.settleEntry(await fullLoad);
  assert.equal(visible.retry, true);
  assert.equal(visible.locked, true);
  assert.equal(visible.status, "incomplete-retry");

  winningReload.resolve({ revision: "winning-B" });
  await reload;
  assert.equal(visibleRevision, "winning-B");
  assert.deepEqual(visible, {
    locked: false,
    retry: false,
    status: "synchronized",
    tone: "",
  });
});

function exactDraftHarness(createRenderer) {
  const state = {
    model: "old-model",
    preset: "custom",
    sharedModel: "old-embedding",
    probe: { ok: true },
    errors: ["stale validation"],
    credential: "keep",
  };
  const visible = {
    row: "old-model / custom / Probe passed",
    probe: "Probe passed",
    errors: ["stale validation"],
    inlineErrors: ["stale validation"],
    credential: "keep",
    inspectorRebuilds: 0,
  };
  const renderer = createRenderer({
    clearInlineErrors() { visible.inlineErrors = []; },
    renderErrorSummary() { visible.errors = [...state.errors]; },
    renderRouteList() {
      visible.row = `${state.model} / ${state.preset} / ${
        state.probe?.ok ? "Probe passed" : "Not probed"
      } / ${state.sharedModel}`;
    },
    renderProbeStatus() { visible.probe = state.probe?.ok ? "Probe passed" : "Not probed"; },
    renderInspector() { visible.inspectorRebuilds += 1; },
    renderCredential() { visible.credential = state.credential; },
  });
  return { state, visible, renderer };
}

test("detail draft edits and Back expose current model/preset with cleared health and errors", () => {
  const createRenderer = requiredExport("createExactDraftRenderCoordinator");
  const { state, visible, renderer } = exactDraftHarness(createRenderer);

  state.model = "current-model";
  state.probe = null;
  state.errors = [];
  renderer.afterDraftMutation();
  assert.equal(visible.inspectorRebuilds, 0, "live text input must not be rebuilt");
  assert.deepEqual(visible.inlineErrors, []);

  state.preset = "openai";
  renderer.afterDraftMutation({ rebuildInspector: true });
  renderer.beforeRouteList();

  assert.match(visible.row, /current-model \/ openai \/ Not probed/);
  assert.equal(visible.probe, "Not probed");
  assert.deepEqual(visible.errors, []);
});

test("Back navigation alone preserves current server validation errors", () => {
  const createRenderer = requiredExport("createExactDraftRenderCoordinator");
  const { visible, renderer } = exactDraftHarness(createRenderer);

  renderer.beforeRouteList();

  assert.deepEqual(visible.errors, ["stale validation"]);
  assert.deepEqual(visible.inlineErrors, ["stale validation"]);
});

test("credential and shared embedding edits refresh visible exact-probe and provider rows", () => {
  const createRenderer = requiredExport("createExactDraftRenderCoordinator");
  const { state, visible, renderer } = exactDraftHarness(createRenderer);

  state.credential = "set";
  state.probe = null;
  state.errors = [];
  renderer.afterDraftMutation({ rerenderCredential: true });
  assert.equal(visible.credential, "set");
  assert.equal(visible.probe, "Not probed");
  assert.match(visible.row, /Not probed/);

  state.sharedModel = "current-embedding";
  renderer.afterDraftMutation();
  assert.match(visible.row, /current-embedding/);
  assert.equal(visible.probe, "Not probed");
});

test("dialog close resolves the current live opener after the shell replaced it", () => {
  const oldOpener = { focusCalls: 0, focus() { this.focusCalls += 1; } };
  const liveOpener = { focusCalls: 0, focus() { this.focusCalls += 1; } };
  const document = {
    activeElement: null,
    addEventListener() {},
    removeEventListener() {},
  };
  const dialog = { querySelectorAll: () => [], focus() {} };
  const controllerInstance = createDialogFocusController({
    dialog,
    document,
    opener: oldOpener,
    resolveOpener: () => liveOpener,
  });

  controllerInstance.activate();
  controllerInstance.deactivate();
  assert.equal(liveOpener.focusCalls, 1);
  assert.equal(oldOpener.focusCalls, 0);
});

test("dialog close does not fall back to a detached opener when resolution fails", () => {
  const detachedOpener = { focusCalls: 0, focus() { this.focusCalls += 1; } };
  const controllerInstance = createDialogFocusController({
    dialog: { querySelectorAll: () => [], focus() {} },
    document: { addEventListener() {}, removeEventListener() {} },
    opener: detachedOpener,
    resolveOpener: () => null,
  });

  controllerInstance.activate();
  controllerInstance.deactivate();
  assert.equal(detachedOpener.focusCalls, 0);
});

test("invalid Runtime numbers produce field feedback and block the save callback", () => {
  const guardRuntime = requiredExport("guardMobileModelRuntime");
  const validateNumbers = requiredExport("validateMobileModelNumbers");
  let requests = 0;
  let visibleErrors = {};
  const accepted = guardRuntime(
    { concurrency: 17, timeout_seconds: 9 },
    (errors) => { visibleErrors = errors; },
  );
  if (accepted) requests += 1;

  assert.equal(accepted, false);
  assert.equal(requests, 0);
  assert.match(visibleErrors.concurrency, /1.*16/);
  assert.match(visibleErrors.timeout_seconds, /10/);
  const unified = validateNumbers(numericState({
    models: {
      ...numericState().models,
      chat: { ...numericState().models.chat, concurrency: 17, timeout_seconds: 9 },
    },
  }));
  assert.equal(visibleErrors.concurrency, unified.byPath["models.chat.concurrency"].message);
  assert.equal(
    visibleErrors.timeout_seconds,
    unified.byPath["models.chat.timeout_seconds"].message,
  );

  const valid = guardRuntime(
    { concurrency: 16, timeout_seconds: 10 },
    (errors) => { visibleErrors = errors; },
  );
  assert.equal(valid, true);
  assert.deepEqual(visibleErrors, {});
});

test("blank numeric inputs remain draft errors while explicit zero keeps zero boundaries", () => {
  const parseDraft = requiredExport("parseMobileModelNumericDraft");
  const validateNumbers = requiredExport("validateMobileModelNumbers");
  const state = numericState();
  state.models.chat.concurrency = parseDraft("");
  state.models.chat.timeout_seconds = parseDraft("");
  state.models.chat.connections[0].num_ctx = parseDraft("");
  state.models.embedding.settings.output_dimensionality = parseDraft("");
  state.models.embedding.settings.similarity_threshold = parseDraft("");

  const blank = validateNumbers(state);
  assert.equal(parseDraft(""), "");
  assert.equal(blank.valid, false);
  assert.equal(Object.keys(blank.byPath).length, 5);

  state.models.chat.concurrency = 1;
  state.models.chat.timeout_seconds = 10;
  state.models.chat.connections[0].num_ctx = parseDraft("0");
  state.models.embedding.settings.output_dimensionality = parseDraft("0");
  state.models.embedding.settings.similarity_threshold = parseDraft("0");
  assert.equal(validateNumbers(state).valid, true);
  assert.equal(state.models.chat.connections[0].num_ctx, 0);
});

test("all model numeric constraints are fieldized before save without clamping the draft", () => {
  const createValidation = requiredExport("createMobileModelNumericValidationController");
  const validateNumbers = requiredExport("validateMobileModelNumbers");
  const state = numericState();
  state.models.chat.concurrency = 1.5;
  state.models.chat.timeout_seconds = 9;
  state.models.chat.connections[0].num_ctx = -1;
  state.models.chat.connections[1].num_ctx = 2048.5;
  state.models.embedding.settings.output_dimensionality = -4;
  state.models.embedding.settings.similarity_threshold = 1.01;
  let saveCalls = 0;
  let visibleErrors = null;
  const validation = createValidation({
    renderErrors(errors) { visibleErrors = errors; },
  });

  const accepted = validation.runSaveIfValid(state, () => { saveCalls += 1; });
  assert.equal(accepted, false);
  assert.equal(saveCalls, 0);
  assert.equal(state.models.chat.connections[0].num_ctx, -1);
  assert.equal(state.models.embedding.settings.output_dimensionality, -4);
  assert.deepEqual(Object.keys(visibleErrors.byPath).sort(), [
    "models.chat.concurrency",
    "models.chat.connections.0.num_ctx",
    "models.chat.connections.1.num_ctx",
    "models.chat.timeout_seconds",
    "models.embedding.settings.output_dimensionality",
    "models.embedding.settings.similarity_threshold",
  ]);
  assert.equal(
    visibleErrors.byConnection["chat-primary"].num_ctx.path,
    "models.chat.connections.0.num_ctx",
  );
  assert.equal(
    visibleErrors.byConnection["chat-fallback"].num_ctx.connectionId,
    "chat-fallback",
  );
  assert.equal(visibleErrors.firstError.path, "models.chat.concurrency");

  state.models.chat.concurrency = 16;
  state.models.chat.timeout_seconds = 10;
  state.models.chat.connections[0].num_ctx = 0;
  state.models.chat.connections[1].num_ctx = 1;
  state.models.embedding.settings.output_dimensionality = 0;
  state.models.embedding.settings.similarity_threshold = 1;
  assert.equal(validation.runSaveIfValid(state, () => { saveCalls += 1; }), true);
  assert.equal(saveCalls, 1);
  assert.equal(visibleErrors.valid, true);

  state.models.embedding.settings.similarity_threshold = Number.POSITIVE_INFINITY;
  assert.equal(validateNumbers(state).valid, false, "similarity must be finite");
  state.models.embedding.settings.similarity_threshold = 0;
  state.models.embedding.settings.output_dimensionality = 12.5;
  assert.equal(validateNumbers(state).valid, false, "dimension must be an integer");
});

test("an invalid numeric draft blocks exact-probe callbacks before serialization", () => {
  const createValidation = requiredExport("createMobileModelNumericValidationController");
  const state = numericState();
  state.models.chat.connections[0].num_ctx = -1;
  let probeCalls = 0;
  const validation = createValidation();

  assert.equal(
    validation.runProbeIfValid(state, () => { probeCalls += 1; }),
    false,
  );
  assert.equal(probeCalls, 0);
  assert.equal(state.models.chat.connections[0].num_ctx, -1);

  state.models.chat.connections[0].num_ctx = 0;
  assert.equal(
    validation.runProbeIfValid(state, () => { probeCalls += 1; }),
    true,
  );
  assert.equal(probeCalls, 1);
});

test("numeric error lifecycle revalidates invalid edits and authoritative replacement", () => {
  const createValidation = requiredExport("createMobileModelNumericValidationController");
  let state = numericState();
  const rendered = [];
  const validation = createValidation({
    getState: () => state,
    renderErrors(errors) { rendered.push(errors); },
  });

  state.models.chat.concurrency = 0;
  validation.afterDraftMutation();
  assert.match(rendered.at(-1).byPath["models.chat.concurrency"].message, /1.*16/);

  state.models.chat.concurrency = 17;
  validation.afterDraftMutation();
  assert.match(
    rendered.at(-1).byPath["models.chat.concurrency"].message,
    /1.*16/,
    "invalid-to-invalid edits must keep truthful feedback",
  );

  state.models.chat.concurrency = 4;
  validation.afterDraftMutation();
  assert.equal(rendered.at(-1).byPath["models.chat.concurrency"], undefined);

  validation.afterDraftMutation();
  assert.equal(rendered.at(-1).valid, true, "unrelated edits must repaint cleared errors");

  state.models.chat.connections[0].num_ctx = -1;
  validation.afterDraftMutation();
  assert.equal(rendered.at(-1).valid, false);
  state = numericState();
  validation.afterAuthoritativeHydration();
  assert.equal(rendered.at(-1).valid, true);
  assert.deepEqual(rendered.at(-1).byConnection, {});
});

test("numeric connection errors treat prototype-like stable IDs as own keys", () => {
  const validateNumbers = requiredExport("validateMobileModelNumbers");
  const state = numericState();
  state.models.chat.connections[0] = { id: "__proto__", num_ctx: -1 };
  state.models.chat.connections[1] = { id: "constructor", num_ctx: -2 };
  const previousPrototypeError = Object.prototype.num_ctx;
  const previousConstructorError = Object.num_ctx;

  try {
    const errors = validateNumbers(state);
    assert.equal(Object.hasOwn(errors.byConnection, "__proto__"), true);
    assert.equal(Object.hasOwn(errors.byConnection, "constructor"), true);
    assert.match(errors.byConnection.__proto__.num_ctx.message, /0/);
    assert.match(errors.byConnection.constructor.num_ctx.message, /0/);
    assert.equal(Object.prototype.num_ctx, previousPrototypeError);
    assert.equal(Object.num_ctx, previousConstructorError);
  } finally {
    if (previousPrototypeError === undefined) delete Object.prototype.num_ctx;
    else Object.prototype.num_ctx = previousPrototypeError;
    if (previousConstructorError === undefined) delete Object.num_ctx;
    else Object.num_ctx = previousConstructorError;
  }
});

test("Pydantic validation details map indexes to stable IDs without echoing values", () => {
  const normalizeDetails = requiredExport("normalizeMobileModelValidationDetails");
  const state = numericState();
  const details = [
    {
      loc: ["body", "models", "chat", "connections", 1, "num_ctx"],
      msg: "SECRET_VALUE",
      type: "greater_than_equal",
      input: "SECRET_VALUE",
    },
    {
      loc: ["body", "models", "embedding", "settings", "output_dimensionality"],
      msg: "SECRET_VALUE",
      type: "int_type",
    },
    {
      loc: ["body", "models", "chat", "connections", 0, "credential", "value"],
      msg: "SECRET_VALUE",
      type: "string_type",
      input: "sk-secret-value",
    },
  ];

  const normalized = normalizeDetails(details, state);
  assert.equal(normalized[0].path, "models.chat.connections.1.num_ctx");
  assert.equal(normalized[0].connection_id, "chat-fallback");
  assert.match(normalized[0].message, /0/);
  assert.equal(
    normalized[1].path,
    "models.embedding.settings.output_dimensionality",
  );
  assert.equal(normalized[2].connection_id, "chat-primary");
  assert.equal(JSON.stringify(normalized).includes("SECRET_VALUE"), false);
  assert.equal(JSON.stringify(normalized).includes("sk-secret-value"), false);
});
