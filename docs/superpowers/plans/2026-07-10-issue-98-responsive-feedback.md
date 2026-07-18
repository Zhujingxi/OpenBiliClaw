# Issue #98 Responsive Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make desktop recommendation and interest/avoidance feedback immediate, locally stable, and truly undoable for 10 seconds while keeping CPU-heavy recommendation work from starving the asyncio event loop.

**Architecture:** A small UMD JavaScript coordinator owns one-shot pending actions and the 10-second commit barrier; `app.js` supplies DOM-specific apply/rollback/commit callbacks without full-list renders. Reshuffle accepts optional visible-content exclusions and dismisses the old batch after swapping. Recommendation MMR and supergroup union-find remain deterministic synchronous helpers wrapped by `asyncio.to_thread()` at async call sites.

**Tech Stack:** Vanilla JavaScript, FastAPI/Pydantic, asyncio, pytest/pytest-asyncio, Node built-ins, Playwright Chromium.

## Global Constraints

- Implement in an isolated worktree created from the latest `origin/main`; do not modify the user's dirty main worktree.
- The durable feedback/probe write starts 10,000 ms after the click; tests may override this with `window.__OBC_TEST_UNDO_WINDOW_MS`.
- `pagehide` flushes pending actions with Fetch `keepalive: true`; timeout/pagehide races may emit at most one request.
- Chat responses are excluded from undo and retain the existing thinking/poll flow.
- No new runtime dependency or config field.
- Identical recommendation inputs must produce identical ordering and supergroup maps before and after offload.
- Real Playwright end-to-end tests and the complete pytest suite must pass before completion.

---

### Task 1: Pending-action commit barrier

**Files:**
- Create: `src/openbiliclaw/web/desktop/assets/js/pending-actions.js`
- Modify: `src/openbiliclaw/web/desktop/index.html`
- Create: `tests/test_desktop_pending_actions.py`

**Interfaces:**
- Consumes: injected `windowMs`, `setTimer`, `clearTimer`, and `onCommitError` functions.
- Produces: `OpenBiliClawPendingActions.createPendingActionCoordinator()` with `schedule(key, action)`, `undo(key)`, `flushAll()`, `has(key)`, and `get(key)`.
- Each action supplies `commit({keepalive})`, `rollback({reason, error})`, and optional `committed()` callbacks.

- [ ] **Step 1: Write the failing Node-backed pytest**

Create a pytest that loads the UMD file through Node and runs fake-timer assertions:

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path


SCRIPT = Path("src/openbiliclaw/web/desktop/assets/js/pending-actions.js")


def test_pending_action_coordinator_commit_undo_failure_and_flush() -> None:
    node = f"""
const assert = require('node:assert/strict');
const {{ createPendingActionCoordinator }} = require({json.dumps(str(SCRIPT.resolve()))});
const timers = new Map(); let nextTimer = 1;
const setTimer = (fn) => {{ const id = nextTimer++; timers.set(id, fn); return id; }};
const clearTimer = (id) => timers.delete(id);
const fire = async (id) => {{ const fn = timers.get(id); timers.delete(id); fn(); await Promise.resolve(); await Promise.resolve(); }};
const commits = []; const rollbacks = []; const committed = [];
const coordinator = createPendingActionCoordinator({{ windowMs: 10000, setTimer, clearTimer }});
assert.equal(coordinator.schedule('a', {{
  commit: (options) => commits.push(['a', options.keepalive]),
  rollback: (details) => rollbacks.push(['a', details.reason]),
  committed: () => committed.push('a'),
}}), true);
assert.equal(coordinator.schedule('a', {{ commit() {{ throw new Error('duplicate'); }}, rollback() {{}} }}), false);
assert.equal(commits.length, 0);
assert.equal(coordinator.undo('a'), true);
assert.deepEqual(rollbacks, [['a', 'undo']]);
assert.equal(commits.length, 0);
assert.equal(coordinator.schedule('b', {{ commit: (options) => commits.push(['b', options.keepalive]), rollback() {{}}, committed: () => committed.push('b') }}), true);
await fire(coordinator.get('b').timerId);
assert.deepEqual(commits, [['b', false]]);
assert.deepEqual(committed, ['b']);
assert.equal(coordinator.schedule('c', {{ commit: () => Promise.reject(new Error('boom')), rollback: (details) => rollbacks.push(['c', details.reason]) }}), true);
await fire(coordinator.get('c').timerId);
assert.deepEqual(rollbacks.at(-1), ['c', 'error']);
assert.equal(coordinator.schedule('d', {{ commit: (options) => commits.push(['d', options.keepalive]), rollback() {{}} }}), true);
const flushPromise = coordinator.flushAll();
await flushPromise;
assert.deepEqual(commits.at(-1), ['d', true]);
assert.equal(coordinator.undo('d'), false);
""";
    subprocess.run(["node", "--input-type=commonjs", "-e", node], check=True)
```

- [ ] **Step 2: Run the test and verify RED**

Run: `uv run --frozen pytest tests/test_desktop_pending_actions.py -q`

Expected: FAIL because `pending-actions.js` does not exist.

- [ ] **Step 3: Implement the coordinator and load it before app.js**

Implement the state machine in the new UMD file:

```javascript
(function installPendingActions(global) {
  "use strict";

  function createPendingActionCoordinator(options = {}) {
    const windowMs = Number(options.windowMs ?? 10000);
    const setTimer = options.setTimer || global.setTimeout.bind(global);
    const clearTimer = options.clearTimer || global.clearTimeout.bind(global);
    const onCommitError = options.onCommitError || (() => {});
    const entries = new Map();

    function finish(key, { keepalive = false } = {}) {
      const entry = entries.get(key);
      if (!entry || entry.state !== "pending") return entry?.promise || Promise.resolve(false);
      entry.state = "committing";
      clearTimer(entry.timerId);
      entry.promise = Promise.resolve()
        .then(() => entry.commit({ keepalive }))
        .then(() => {
          entry.state = "committed";
          entries.delete(key);
          entry.committed?.();
          return true;
        })
        .catch((error) => {
          entry.state = "rolled_back";
          entries.delete(key);
          entry.rollback({ reason: "error", error });
          onCommitError(error, key);
          return false;
        });
      return entry.promise;
    }

    function schedule(key, action) {
      if (!key || entries.has(key)) return false;
      const entry = { ...action, key, state: "pending", promise: null, timerId: null };
      entry.timerId = setTimer(() => { void finish(key); }, windowMs);
      entries.set(key, entry);
      return true;
    }

    function undo(key) {
      const entry = entries.get(key);
      if (!entry || entry.state !== "pending") return false;
      clearTimer(entry.timerId);
      entries.delete(key);
      entry.state = "rolled_back";
      entry.rollback({ reason: "undo", error: null });
      return true;
    }

    function flushAll() {
      return Promise.all([...entries.keys()].map((key) => finish(key, { keepalive: true })));
    }

    return { schedule, undo, flushAll, has: (key) => entries.has(key), get: (key) => entries.get(key) || null };
  }

  const api = { createPendingActionCoordinator };
  global.OpenBiliClawPendingActions = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
```

Add `<script src="/web/assets/js/pending-actions.js" defer></script>` immediately before the app script.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `uv run --frozen pytest tests/test_desktop_pending_actions.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/openbiliclaw/web/desktop/assets/js/pending-actions.js src/openbiliclaw/web/desktop/index.html tests/test_desktop_pending_actions.py
git commit -m "feat(web): add undoable pending action coordinator"
```

### Task 2: Recommendation-card optimistic feedback and true undo

**Files:**
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css`
- Create: `tests/test_desktop_web_issue_98_e2e.py`
- Modify: `tests/test_desktop_web_pool_status.py`

**Interfaces:**
- Consumes: `OpenBiliClawPendingActions.createPendingActionCoordinator()` from Task 1 and existing `/api/feedback`.
- Produces: `feedbackActionKey(item)`, `stageRecommendationFeedback(action, item, card)`, local undo controls, and pagehide flush.

- [ ] **Step 1: Write the failing desktop browser test**

Create a `ThreadingHTTPServer` fixture that serves `/web/`, all desktop assets, three recommendation items, and records `/api/feedback` bodies. Launch Playwright Chromium with:

```python
page.add_init_script("window.__OBC_TEST_UNDO_WINDOW_MS = 250;")
page.goto(f"{base_url}/web/")
cards = page.locator("#videoGrid .video-card")
cards.first.locator('[data-action="like"]').click()
expect(cards.first.locator(".status-line")).to_contain_text("撤销")
assert stub.feedback_posts == []
assert cards.nth(1).bounding_box() == second_box_before
cards.first.locator('[data-feedback-undo]').click()
page.wait_for_timeout(350)
assert stub.feedback_posts == []
```

Add a second scenario that lets the timer expire while the server delays its response, clicks the second card during that delay, asserts stable card identity/position, then verifies exactly one feedback request. Add a 500-response scenario asserting the first card returns to its pre-click button/status state.

- [ ] **Step 2: Run the E2E test and verify RED**

Run: `uv run --frozen pytest tests/test_desktop_web_issue_98_e2e.py -q -m integration`

Expected: FAIL because the existing handler waits for the network and exposes no undo button.

- [ ] **Step 3: Implement local card mutation and delayed commit**

Instantiate one coordinator after `showToast()` is available:

```javascript
const pendingActions = window.OpenBiliClawPendingActions.createPendingActionCoordinator({
  windowMs: Number(window.__OBC_TEST_UNDO_WINDOW_MS || 10000),
  onCommitError: (error) => showToast(configErrorMessage(error?.details) || error?.message || "反馈提交失败，已恢复原状态。"),
});
window.addEventListener("pagehide", () => { void pendingActions.flushAll(); });
```

Use stable identity and an explicit keepalive-capable submit:

```javascript
function feedbackActionKey(item) {
  const contentId = item?.bvid || item?.content_id;
  if (!contentId) return "";
  const platform = String(item?.platform || item?.source_platform || "").trim().toLowerCase();
  return `recommendation:${platform}:${contentId}`;
}

function submitFeedback(item, feedbackType, note = "", { keepalive = false } = {}) {
  return requestJsonStrict(ENDPOINTS.feedback, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recommendation_id: item.id, feedback_type: feedbackType, note }),
    timeoutMs: 30000,
    keepalive,
  });
}
```

Refactor the like/dislike/dismiss branch so it:

1. snapshots `item.feedback_type`, button disabled/pressed/active states, card classes, and status text;
2. updates only that card and appends `<button data-feedback-undo>` to status-line;
3. schedules `submitFeedback()` as commit;
4. restores the snapshot on undo/error;
5. removes only the undo button on commit, leaving negative cards in the grid until the next deliberate list operation;
6. never calls `renderAll()` from the click, commit, or rollback path.

Keep comments fire-and-forget with local failure recovery but no 10-second undo.

- [ ] **Step 4: Add pending/undo CSS and replace brittle source-contract assertions**

Add `.is-feedback-pending`, `[data-feedback-undo]`, and reduced-motion-safe styles. Replace the existing regex that searches for a distant `catch` block with assertions for `pendingActions.schedule`, stable key construction, local undo, and absence of `renderAll()` inside the extracted recommendation feedback helper.

- [ ] **Step 5: Run focused unit and E2E tests**

Run:

```bash
uv run --frozen pytest tests/test_desktop_pending_actions.py tests/test_desktop_web_pool_status.py -q
uv run --frozen pytest tests/test_desktop_web_issue_98_e2e.py -q -m integration
```

Expected: PASS, with the Playwright test executed rather than skipped.

- [ ] **Step 6: Commit**

```bash
git add src/openbiliclaw/web/desktop/assets/js/app.js src/openbiliclaw/web/desktop/assets/css/app.css tests/test_desktop_web_pool_status.py tests/test_desktop_web_issue_98_e2e.py
git commit -m "fix(web): make recommendation feedback instant and undoable"
```

### Task 3: Interest and avoidance probe optimistic feedback

**Files:**
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: `tests/test_desktop_web_issue_98_e2e.py`
- Modify: `tests/test_desktop_web_probe_defer.py`

**Interfaces:**
- Consumes: the Task 1 coordinator and existing interest/avoidance respond APIs.
- Produces: `probePendingKey(type, domain)`, reusable message/profile action binders, and local probe apply/rollback/commit flows.

- [ ] **Step 1: Extend the browser test and verify RED**

Have the stub profile return one interest and one avoidance probe. Cover both the messages drawer and profile speculation row:

```python
probe_button.click()
expect(probe_row).to_contain_text("撤销")
assert stub.probe_posts == []
undo_button.click()
expect(probe_row.locator('[data-probe="confirm"]')).to_be_visible()
page.wait_for_timeout(350)
assert stub.probe_posts == []
```

Then let confirm expire and assert exactly one POST with the correct endpoint/domain/response. Return 500 for another probe and assert the original buttons are restored.

- [ ] **Step 2: Refactor action binding and implement pending probe state**

Extract binders so rollback can restore listeners without a full render:

```javascript
function bindMessageProbeActions(msg, el) {
  el.querySelectorAll("[data-probe]").forEach((button) => {
    button.addEventListener("click", () => respondProbe(msg, button.dataset.probe, el));
  });
}

function bindSpeculativeRowActions(row) {
  row.querySelectorAll("[data-spec-response]").forEach((button) => {
    button.addEventListener("click", () => respondSpeculativeInterest(button));
  });
}
```

For confirm/reject/defer, snapshot the local action container, add the handled key, replace only that container with result text + undo, and schedule the existing request. Undo/error restores the snapshot, removes the handled key, and calls the matching binder. Commit finalizes the result locally and schedules profile refresh without rebuilding the messages list. `chat` bypasses the coordinator unchanged.

- [ ] **Step 3: Update source-contract coverage**

Assert both probe surfaces call `pendingActions.schedule`, share `probePendingKey`, expose undo, and keep `chat` on the immediate dialogue path.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
uv run --frozen pytest tests/test_desktop_web_probe_defer.py -q
uv run --frozen pytest tests/test_desktop_web_issue_98_e2e.py -q -m integration
```

Expected: PASS without skipped Playwright cases.

- [ ] **Step 5: Commit**

```bash
git add src/openbiliclaw/web/desktop/assets/js/app.js tests/test_desktop_web_probe_defer.py tests/test_desktop_web_issue_98_e2e.py
git commit -m "fix(web): make interest and avoidance probes undoable"
```

### Task 4: Non-blocking reshuffle with visible-card exclusion

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_recommendation_engine.py`
- Modify: `tests/test_desktop_web_pool_status.py`

**Interfaces:**
- Produces: `RecommendationReshuffleIn(excluded_bvids: list[str])` and `RecommendationEngine.reshuffle_recommendations(..., excluded_bvids=frozenset())`.
- Preserves: POST `/api/recommendations/reshuffle` with no body.

- [ ] **Step 1: Add failing API/engine/frontend tests**

Add tests proving:

- no-body reshuffle still succeeds;
- JSON `excluded_bvids` reaches the fake recommendation engine;
- exclusions larger than the old 40-row read window still permit a full result when the pool is large enough;
- an excluded row reintroduced by `_apply_platform_floor` is removed by the final in-memory guard;
- desktop sends exclusions regardless of the dismiss toggle and does not await the dismiss batch before rendering fresh cards.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run --frozen pytest tests/test_api_app.py -q -k reshuffle
uv run --frozen pytest tests/test_recommendation_engine.py -q -k 'reshuffle or excluded'
uv run --frozen pytest tests/test_desktop_web_pool_status.py -q -k reshuffle
```

Expected: FAIL on the missing request model/signature and serial dismiss flow.

- [ ] **Step 3: Implement optional exclusions and refill-safe candidate loading**

Add:

```python
class RecommendationReshuffleIn(BaseModel):
    """Optional visible-card exclusions for a reshuffle request."""

    excluded_bvids: list[str] = Field(default_factory=list)
```

Accept `payload: RecommendationReshuffleIn | None = Body(default=None)`, normalize it to a frozenset, and pass it through reshuffle to `serve()`. In `serve()`, size the read as `max(limit * multiplier, 40) + len(excluded_bvids)`, keep the final exclusion after platform floor top-up, then apply the remaining filters.

- [ ] **Step 4: Implement swap-first desktop flow**

Compute visible keys/bvids independently of the dismiss toggle, POST exclusions, replace/render only on a non-empty fresh batch, then fire-and-forget dismiss old cards if enabled. Batch dismiss failures only toast and never write `state.videos`. Increment a list generation only when replacing/hydrating the list; guard every delayed list mutation with its captured generation.

- [ ] **Step 5: Run focused tests and verify GREEN**

Repeat the three Step 2 commands. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py src/openbiliclaw/recommendation/engine.py src/openbiliclaw/web/desktop/assets/js/app.js tests/test_api_app.py tests/test_recommendation_engine.py tests/test_desktop_web_pool_status.py
git commit -m "perf(reshuffle): swap before background dismiss"
```

### Task 5: Deterministic CPU offload and responsiveness regression tests

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Modify: `tests/test_recommendation_engine.py`
- Modify: `tests/test_api_app.py`

**Interfaces:**
- Produces: `_select_diversified_batch_async(...)` and `_build_supergroup_canonical_map_async(...)` wrappers.
- Preserves: synchronous helper outputs and public engine behavior.

- [ ] **Step 1: Write failing determinism and event-loop responsiveness tests**

Compare sync and async helper results for the same candidates/embeddings. Monkeypatch the sync selector with a helper that records `threading.get_ident()` and blocks briefly; start the async wrapper plus a 10 ms heartbeat and assert the heartbeat advances before the helper completes and the worker thread differs from the event-loop thread. Repeat output equivalence for the supergroup map.

Add an API regression test with a soul engine whose `process_feedback_batch_if_needed()` blocks on an event: POST `/api/feedback` must return before that event is released, proving the LLM batch remains scheduled rather than inline.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run --frozen pytest tests/test_recommendation_engine.py -q -k 'offload or responsive or supergroup'
uv run --frozen pytest tests/test_api_app.py -q -k 'feedback and background'
```

Expected: FAIL because the async wrappers do not exist.

- [ ] **Step 3: Extract sync supergroup construction and add async wrappers**

Keep union-find in `_build_supergroup_canonical_map(...)`. Add wrappers using `await asyncio.to_thread(cls._sync_helper, ...)`, measure `time.perf_counter()`, and warn when elapsed exceeds 50 ms. Change `serve()` and prewarm to await wrappers. Do not alter helper ordering, thresholds, caps, or return values.

- [ ] **Step 4: Run focused tests and verify GREEN**

Repeat Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/openbiliclaw/recommendation/engine.py tests/test_recommendation_engine.py tests/test_api_app.py
git commit -m "perf(recommendation): yield CPU selection off the event loop"
```

### Task 6: Required documentation

**Files:**
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/changelog.md`
- Modify: `README.md`
- Modify: `README_EN.md`

**Interfaces:**
- Documents the exact public API, interaction semantics, GIL limitation, and user-visible issue #98 fix.

- [ ] **Step 1: Update module documentation and changelog**

Document:

- runtime: 10-second pending-action coordinator, pagehide keepalive, local rollback, and threaded time-slicing;
- recommendation: stable card feedback, optional reshuffle exclusions, swap-first dismiss, unchanged deterministic ranking;
- soul: confirm/reject/defer commit barrier on both desktop probe surfaces, chat exclusion, and background LLM boundary;
- changelog: add issue #98 under the current top version block.

- [ ] **Step 2: Add the user-visible README callout in both languages**

Add a concise bullet to the current top important-update block: desktop feedback is instant, layout-stable, undoable for 10 seconds, and no longer starved by recommendation CPU loops.

- [ ] **Step 3: Validate documentation consistency**

Run:

```bash
rg -n "10 秒|issue #98|excluded_bvids|to_thread" docs/modules docs/changelog.md README.md
rg -n "10-second|issue #98|excluded_bvids|to_thread" README_EN.md
git diff --check
```

Expected: all intended references exist; no whitespace errors.

- [ ] **Step 4: Commit**

```bash
git add docs/modules/runtime.md docs/modules/recommendation.md docs/modules/soul.md docs/changelog.md README.md README_EN.md
git commit -m "docs: document responsive feedback and CPU isolation"
```

### Task 7: End-to-end and complete acceptance

**Files:**
- Verify all changed files.

**Interfaces:**
- Produces the final evidence required to close issue #98.

- [ ] **Step 1: Run formatting, lint, and typing**

Run:

```bash
uv run --frozen ruff format --check src/ tests/
uv run --frozen ruff check src/ tests/
uv run --frozen mypy src/
```

Expected: all commands exit 0.

- [ ] **Step 2: Run the real browser end-to-end suite**

Run:

```bash
uv run --frozen pytest tests/test_desktop_web_issue_98_e2e.py -vv -m integration
```

Expected: all issue #98 Playwright cases PASS and none are skipped. If Chromium is missing, install the project-pinned browser with `uv run --frozen python -m playwright install chromium`, rerun, and require PASS.

- [ ] **Step 3: Run all focused regression suites**

Run:

```bash
uv run --frozen pytest tests/test_desktop_pending_actions.py tests/test_desktop_web_pool_status.py tests/test_desktop_web_probe_defer.py tests/test_api_app.py tests/test_recommendation_engine.py -q
```

Expected: PASS.

- [ ] **Step 4: Run the complete test suite**

Run: `uv run --frozen pytest`

Expected: PASS with no failures; report any intentional environment skips separately, while the issue #98 E2E test itself must not skip.

- [ ] **Step 5: Inspect final diff and commit any verification-only fixes**

Run:

```bash
git status --short
git diff --check
git log --oneline --decorate -8
```

If verification required code changes, repeat the affected RED/GREEN command and commit only those fixes with a scoped Conventional Commit message.
