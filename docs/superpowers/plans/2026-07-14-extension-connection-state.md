# Extension Connection State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the popup connection badge from oscillating between connected and disconnected when HTTP remains reachable but the runtime WebSocket reconnects.

**Architecture:** Add a revision-guarded three-state coordinator to the existing popup connection module. Keep `state.online` as HTTP reachability, project `online / reconnecting / offline` into the header badge, and let only a failed `/api/ping` start the offline poller.

**Tech Stack:** Browser-extension JavaScript, TypeScript-stripped Node test runner, HTML/CSS, npm build scripts.

## Global Constraints

- Do not change backend routes or payloads.
- Keep Chrome and Firefox on the same `popup/` resources.
- `online` and `reconnecting` both mean HTTP API calls remain available; only `offline` sets `state.online=false`.
- A stale disconnect probe must never overwrite a newer stream connection.
- Update `docs/modules/extension.md` and the current `docs/changelog.md` release block.

---

### Task 1: Add the tested three-state connection model

**Files:**
- Modify: `extension/tests/popup-connection-poller.test.ts`
- Modify: `extension/tests/popup-helpers.test.ts`
- Modify: `extension/popup/popup-connection-poller.js`
- Modify: `extension/popup/popup-helpers.js`

**Interfaces:**
- Consumes: existing `checkBackendStatus(): Promise<boolean>`.
- Produces: `BACKEND_CONNECTION_STATUS`, `createBackendConnectionCoordinator({ checkBackendStatus, onStatusChange })`, and `getConnectionBadgeState(status)`.

- [x] **Step 1: Write failing coordinator and badge tests**

Add tests that assert:

```ts
assert.deepEqual(getConnectionBadgeState("reconnecting"), {
  tone: "reconnecting",
  label: "重连中",
});

const coordinator = connectionModule.createBackendConnectionCoordinator({
  checkBackendStatus: async () => true,
  onStatusChange(status: string) {
    statuses.push(status);
  },
});
coordinator.markStreamConnected();
const result = await coordinator.markStreamDisconnected();
assert.deepEqual(statuses, ["online", "reconnecting"]);
assert.deepEqual(result, {
  applied: true,
  reachable: true,
  status: "reconnecting",
});
```

Also cover failed / throwing probes and a deferred failed probe made stale by `markStreamConnected()`.

- [x] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
cd extension
node --test --experimental-strip-types \
  tests/popup-connection-poller.test.ts \
  tests/popup-helpers.test.ts
```

Expected: FAIL because `createBackendConnectionCoordinator` is not exported and the reconnecting badge projection does not exist.

- [x] **Step 3: Implement the minimal pure model**

Add explicit status constants and a coordinator whose methods are:

```js
markHttpReachable();
markOffline();
markStreamConnected();
await markStreamDisconnected();
getStatus();
```

Each synchronous method increments a revision. `markStreamDisconnected()` publishes `reconnecting`, awaits `checkBackendStatus()`, ignores its result when the revision changed, and otherwise publishes `offline` only for false / thrown probes. Update `getConnectionBadgeState(status)` to return the three documented projections.

- [x] **Step 4: Run the focused tests and confirm GREEN**

Run the same Node command. Expected: all focused tests pass with no warnings.

### Task 2: Wire the coordinator into the popup and render reconnecting

**Files:**
- Modify: `extension/tests/popup-layout.test.ts`
- Modify: `extension/tests/popup-scroll.test.ts`
- Modify: `extension/tests/popup-stream.test.ts`
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup-stream.js`

**Interfaces:**
- Consumes: Task 1 coordinator methods and badge projection.
- Produces: popup lifecycle where HTTP reachability controls `state.online` and the header renders an amber reconnecting state.

- [x] **Step 1: Write failing popup wiring and style contracts**

Add source-contract assertions that require:

```ts
assert.match(popupJs, /createBackendConnectionCoordinator/);
assert.match(popupJs, /markStreamConnected\(\)/);
assert.match(popupJs, /markStreamDisconnected\(\)/);
assert.match(popupHtml, /\.status-badge\[data-tone="reconnecting"\]/);
assert.match(popupHtml, /\.status-dot\.reconnecting/);
```

- [x] **Step 2: Run the focused popup tests and confirm RED**

Run:

```bash
cd extension
node --test --experimental-strip-types tests/popup-layout.test.ts
```

Expected: FAIL because the coordinator wiring and reconnecting CSS are absent.

- [x] **Step 3: Implement popup lifecycle wiring and styling**

In `popup.js`:

- Instantiate the coordinator next to `offlineBackendPoller`.
- In `onStatusChange`, set `state.online = status !== "offline"`, update the header, start the poller only for `offline`, and stop it otherwise.
- Map initial and settings-page HTTP checks to `markHttpReachable()` / `markOffline()`.
- Map stream callbacks to `markStreamConnected()` / `markStreamDisconnected()`.
- Keep recommendation refresh behavior unchanged: skip the first stream-open refresh after startup data loads, but refresh on later reconnects.
- Make `setStatus(status)` toggle both `offline` and `reconnecting` dot classes.

In `popup.html`, add an amber badge background and amber dot for `reconnecting` without changing layout dimensions.

In `popup-stream.js`, suppress `onDisconnect` for an intentional client shutdown so changing the configured backend does not create a false disconnect transition.

- [x] **Step 4: Run all focused test files and confirm GREEN**

Run:

```bash
cd extension
node --test --experimental-strip-types \
  tests/popup-connection-poller.test.ts \
  tests/popup-helpers.test.ts \
  tests/popup-layout.test.ts \
  tests/popup-scroll.test.ts \
  tests/popup-stream.test.ts
```

Expected: all focused tests pass.

### Task 3: Synchronize docs and verify both extension targets

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`
- Modify: `docs/superpowers/plans/2026-07-14-extension-connection-state.md`

**Interfaces:**
- Consumes: the final three-state behavior from Tasks 1-2.
- Produces: repository documentation and verification evidence for Chrome and Firefox builds.

- [x] **Step 1: Update user-facing technical documentation**

Document that `/api/ping` owns reachability, runtime-stream disconnect enters `reconnecting`, only failed HTTP probes enter `offline`, and stale probes are revision-guarded. Add a short bullet under the current v0.3.165 changelog block.

- [x] **Step 2: Run the full extension test and typecheck suites**

Run:

```bash
cd extension
npm test
npm run typecheck
```

Expected: zero failing tests and zero TypeScript errors.

- [x] **Step 3: Build both browser targets**

Run:

```bash
cd extension
npm run build
npm run build:firefox
```

Expected: both builds exit 0.

- [x] **Step 4: Inspect the final diff and documentation checklist**

Run:

```bash
git diff --check
git status --short
git diff -- extension/popup extension/tests docs/modules/extension.md docs/changelog.md
```

Expected: only scoped implementation, tests, plan, module documentation, and changelog changes; `.playwright-cli/` remains untouched.

- [x] **Step 5: Commit the implementation**

```bash
git add \
  extension/popup/popup-connection-poller.js \
  extension/popup/popup-helpers.js \
  extension/popup/popup.js \
  extension/popup/popup.html \
  extension/popup/popup-stream.js \
  extension/tests/popup-connection-poller.test.ts \
  extension/tests/popup-helpers.test.ts \
  extension/tests/popup-layout.test.ts \
  extension/tests/popup-scroll.test.ts \
  extension/tests/popup-stream.test.ts \
  docs/modules/extension.md \
  docs/changelog.md \
  docs/superpowers/plans/2026-07-14-extension-connection-state.md
git commit -m "fix(extension): stabilize backend connection status"
```
