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

  const valid = guardRuntime(
    { concurrency: 16, timeout_seconds: 10 },
    (errors) => { visibleErrors = errors; },
  );
  assert.equal(valid, true);
  assert.deepEqual(visibleErrors, {});
});
