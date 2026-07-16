# Web Empty Recommendation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mobile and desktop Web recover from transient recommendation/runtime-status timeouts without presenting failures as real empty inventory or replacing an existing recommendation list.

**Architecture:** Each Web surface keeps independent failure state for recommendation data and runtime status. A bounded single-flight retry controller retries only failed resources, and recommendation recovery is additionally gated on the visible list still being empty; successful empty arrays are terminal real-empty results. Runtime stream pool snapshots can heal runtime-status failure, while broad hydration remains reserved for existing config/init flows.

**Tech Stack:** Browser JavaScript, FastAPI-served static assets, Python static regression tests, Node `node:test`, Ruff.

## Global Constraints

- Cover both `/m` and `/web`; do not change extension behavior.
- Preserve the rule that `refresh.pool_updated` is not a general recommendation-list replacement signal.
- Retry delays are exactly 1s, 2s, 4s, and 8s; at most four automatic attempts per recovery round.
- A successful `items=[]` response is a real empty result and must stop recommendation retries.
- Existing or appended cards must never be replaced by automatic recovery.
- Update `docs/modules/recommendation.md` and the current `docs/changelog.md` release block.
- Do not touch unrelated dirty version/release files.

---

## File Structure

- `src/openbiliclaw/web/js/views/recommend.js`: mobile recommendation/runtime failure state, retry scheduling, empty/error UI, stream/tab recovery triggers.
- `src/openbiliclaw/web/js/app.js`: notify the mobile recommendation view when runtime stream reconnects.
- `src/openbiliclaw/web/desktop/assets/js/app.js`: desktop resource-specific read results, bounded recovery scheduler, empty/error UI, stream recovery triggers.
- `tests/test_mobile_recommend_load_resilience.py`: mobile source-contract regressions for failure distinction and retry gating.
- `tests/test_desktop_web_pool_status.py`: desktop source-contract regressions for resource-specific recovery and list preservation.
- `extension/tests/runtime-refresh-coalescing.test.ts`: cross-surface guard that pool events may recover an empty failed list but may not cause unconditional replacement.
- `docs/modules/recommendation.md`: implemented behavior and failure semantics.
- `docs/changelog.md`: user-facing fix entry under v0.3.162.

### Task 1: Mobile Web bounded recovery

**Files:**
- Modify: `tests/test_mobile_recommend_load_resilience.py`
- Modify: `extension/tests/runtime-refresh-coalescing.test.ts`
- Modify: `src/openbiliclaw/web/js/views/recommend.js`
- Modify: `src/openbiliclaw/web/js/app.js`
- Test: `tests/test_mobile_recommend_load_resilience.py`

**Interfaces:**
- Consumes: `fetchRecommendations()`, `fetchRuntimeStatus()`, `normalizeRuntimeStatus()`, `mergeRuntimeStatusEvent()`.
- Produces: exported `onStreamConnect()` hook from `views/recommend.js`; internal `scheduleRecommendationRecovery()`, `scheduleRuntimeStatusRecovery()`, and resource success/failure state.

- [ ] **Step 1: Write failing mobile source-contract tests**

Replace the old assertion that requires `await fetchRecommendations().catch(() => [])` with assertions that require explicit `try/catch`, a distinct failed state, bounded delays, and an empty-list guard. Add a reconnect contract:

```python
def test_mobile_recommend_failure_is_not_coerced_to_empty_success() -> None:
    recommend_js = Path("src/openbiliclaw/web/js/views/recommend.js").read_text()

    assert "await fetchRecommendations().catch(() => [])" not in recommend_js
    assert 'recommendationLoadState = "failed"' in recommend_js
    assert "scheduleRecommendationRecovery" in recommend_js
    assert "state.recommendations.length > 0" in recommend_js


def test_mobile_recovery_is_bounded_and_reconnectable() -> None:
    recommend_js = Path("src/openbiliclaw/web/js/views/recommend.js").read_text()
    app_js = Path("src/openbiliclaw/web/js/app.js").read_text()

    assert "[1000, 2000, 4000, 8000]" in recommend_js
    assert "export function onStreamConnect" in recommend_js
    assert "recStreamConnect()" in app_js
    assert 'runtimeStatusLoadState = "failed"' in recommend_js
```

In `extension/tests/runtime-refresh-coalescing.test.ts`, require the mobile pool block to call
`scheduleRecommendationRecovery()` behind an empty-list/failed-state guard while still rejecting
direct list assignment or `loadData()`.

- [ ] **Step 2: Run the mobile tests and verify RED**

Run:

```bash
pytest tests/test_mobile_recommend_load_resilience.py -q
cd extension && node --test --experimental-strip-types tests/runtime-refresh-coalescing.test.ts
```

Expected: FAIL because failures are still coerced to `[]` and no recovery state/hooks exist.

- [ ] **Step 3: Implement mobile resource state and scheduler**

In `recommend.js`, add resource states and exact retry delays:

```js
const RECOVERY_DELAYS_MS = [1000, 2000, 4000, 8000];
let recommendationLoadState = "idle";
let runtimeStatusLoadState = "idle";
let recommendationRecoveryAttempt = 0;
let runtimeStatusRecoveryAttempt = 0;
let recommendationRecoveryTimer = null;
let runtimeStatusRecoveryTimer = null;
let recommendationRecoveryInFlight = false;
let runtimeStatusRecoveryInFlight = false;
```

Implement recommendation recovery so it returns immediately when the list is non-empty, when the last result was a successful empty array, or when a request/timer already exists. Each failed attempt schedules the next delay; attempt four leaves `failed-exhausted`. Any successful array clears timer/attempt/failure state and updates cards only while the list is still empty.

Implement runtime-status recovery independently. A successful HTTP response or a `refresh.pool_updated` payload carrying `pool_available_count` clears the runtime recovery state. Do not couple runtime recovery to recommendation replacement.

Change `loadData()` to use explicit `try/catch`:

```js
try {
  const recs = await fetchRecommendations();
  applyRecoveredRecommendations(recs);
} catch {
  recommendationLoadState = "failed";
  scheduleRecommendationRecovery();
}
```

Move the runtime side-channel request through the same success/failure helpers. Render “推荐加载失败，正在重试” while a round is active and “推荐加载失败，点此重试” after exhaustion; a click starts a fresh round.

Export `onStreamConnect()` and call it from `app.js`'s stream `onConnect()`. `initRecommendView()` should also start a fresh round on tab re-entry only when the list is empty and the resource remains failed.

- [ ] **Step 4: Run the mobile tests and verify GREEN**

Run:

```bash
pytest tests/test_mobile_recommend_load_resilience.py -q
node --check src/openbiliclaw/web/js/views/recommend.js
node --check src/openbiliclaw/web/js/app.js
```

Expected: all commands PASS.

- [ ] **Step 5: Commit mobile recovery**

```bash
git add tests/test_mobile_recommend_load_resilience.py extension/tests/runtime-refresh-coalescing.test.ts src/openbiliclaw/web/js/views/recommend.js src/openbiliclaw/web/js/app.js
git commit -m "fix(web): recover mobile recommendation timeouts"
```

### Task 2: Desktop Web bounded recovery

**Files:**
- Modify: `tests/test_desktop_web_pool_status.py`
- Modify: `extension/tests/runtime-refresh-coalescing.test.ts`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Test: `tests/test_desktop_web_pool_status.py`

**Interfaces:**
- Consumes: `requestJsonStrict()`, `normalizeRecommendationList()`, `applyRuntimeStatus()`, `renderVideos()`.
- Produces: internal `readRecommendationSnapshot()`, `scheduleDesktopRecommendationRecovery()`, `scheduleDesktopRuntimeRecovery()`, and resource failure state.

- [ ] **Step 1: Write failing desktop source-contract tests**

Add tests requiring strict resource reads, bounded recovery, and the empty-list gate:

```python
def test_desktop_failed_recommendation_read_schedules_empty_only_recovery() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text()

    assert "readRecommendationSnapshot" in app_js
    assert "scheduleDesktopRecommendationRecovery" in app_js
    assert "if (state.videos.length > 0)" in app_js
    assert 'desktopRecommendationLoadState = "failed"' in app_js


def test_desktop_runtime_failure_recovers_independently() -> None:
    app_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text()

    assert "scheduleDesktopRuntimeRecovery" in app_js
    assert "[1000, 2000, 4000, 8000]" in app_js
    assert 'desktopRuntimeLoadState = "failed"' in app_js
```

Extend `extension/tests/runtime-refresh-coalescing.test.ts` to require conditional desktop
recommendation recovery while preserving the existing prohibition on including
`refresh.pool_updated` in the broad hydration trigger.

- [ ] **Step 2: Run the desktop tests and verify RED**

Run:

```bash
pytest tests/test_desktop_web_pool_status.py -q
cd extension && node --test --experimental-strip-types tests/runtime-refresh-coalescing.test.ts
```

Expected: FAIL because desktop hydration still collapses failed reads to `null` without resource recovery.

- [ ] **Step 3: Implement desktop resource reads and recovery**

Add desktop resource states parallel to mobile and use `requestJsonStrict()` inside resource-specific helpers so thrown errors remain distinguishable from successful empty payloads:

```js
async function readRecommendationSnapshot() {
  const payload = await requestJsonStrict(ENDPOINTS.recommendations, { timeoutMs: 15000 });
  return Array.isArray(payload) ? payload : asArray(payload?.items);
}
```

In initial `hydrateFromBackend()`, use settled results for recommendations and runtime status while leaving unrelated hydration reads best-effort. On recommendation rejection, keep `state.videos` unchanged, mark failure, render the retry error, and schedule empty-only recovery. On success, including an empty list, clear recommendation failure and apply the snapshot. On runtime rejection, keep the prior runtime snapshot and schedule runtime-only recovery.

On runtime-stream open, start new rounds only for failed resources. In `handleRuntimeEvent()`, a pool snapshot clears runtime failure; it calls recommendation recovery only when `state.videos.length === 0` and the recommendation resource is failed. It must never schedule broad hydration or replace a non-empty list.

- [ ] **Step 4: Run desktop tests and verify GREEN**

Run:

```bash
pytest tests/test_desktop_web_pool_status.py -q
node --check src/openbiliclaw/web/desktop/assets/js/app.js
```

Expected: PASS.

- [ ] **Step 5: Commit desktop recovery**

```bash
git add tests/test_desktop_web_pool_status.py extension/tests/runtime-refresh-coalescing.test.ts src/openbiliclaw/web/desktop/assets/js/app.js
git commit -m "fix(web): recover desktop recommendation timeouts"
```

### Task 3: Documentation and integrated regression verification

**Files:**
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: source behavior and regression guards introduced in Tasks 1 and 2.
- Produces: user/developer documentation and integrated verification evidence.

- [ ] **Step 1: Update documentation**

Add an implemented-feature row or behavior bullet to `docs/modules/recommendation.md` stating:

```markdown
- 移动与桌面 Web 会把推荐/库存读取失败与真实空结果分开：瞬时超时进入最多
  1/2/4/8 秒四次的空态恢复；成功空数组终止重试；`refresh.pool_updated`
  只在当前列表仍为空且上次推荐读取失败时触发条件恢复，已有/追加卡片不被覆盖。
```

Add a v0.3.162 changelog bullet describing the user-visible fix. Do not edit the existing release/version files already dirty in the worktree.

- [ ] **Step 2: Run targeted tests and verify GREEN**

Run:

```bash
pytest tests/test_mobile_recommend_load_resilience.py tests/test_desktop_web_pool_status.py -q
cd extension && node --test --experimental-strip-types tests/runtime-refresh-coalescing.test.ts
```

Expected: PASS.

- [ ] **Step 3: Commit documentation**

```bash
git add docs/modules/recommendation.md docs/changelog.md
git commit -m "docs: document web empty-state recovery"
```

### Task 4: Verification

**Files:**
- Verify only; no planned production edits.

**Interfaces:**
- Consumes: all Task 1-3 deliverables.
- Produces: verification evidence for handoff.

- [ ] **Step 1: Run targeted Python tests**

```bash
pytest tests/test_mobile_recommend_load_resilience.py tests/test_desktop_web_pool_status.py tests/test_desktop_web_load_more.py -q
```

Expected: PASS.

- [ ] **Step 2: Run extension tests and typecheck**

```bash
cd extension && npm test
cd extension && npm run typecheck
```

Expected: PASS.

- [ ] **Step 3: Run repository quality checks**

```bash
ruff check src/ tests/
mypy src/
pytest
```

Expected: PASS. If unrelated pre-existing failures occur, record exact commands and output without changing unrelated files.

- [ ] **Step 4: Inspect the final diff**

```bash
git diff --check
git status --short
git log --oneline -4
```

Expected: no whitespace errors; unrelated pre-existing dirty release/version files remain untouched; implementation commits are present.
