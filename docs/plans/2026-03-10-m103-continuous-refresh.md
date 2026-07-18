# 持续候选池刷新与插件通知 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a background refresh loop that keeps `content_cache` and recommendations fresh, and expose one high-confidence recommendation to the extension notification channel.

**Architecture:** Reuse the existing `discover -> content_cache -> recommend` chain. Add a lightweight runtime-state file plus a background loop inside `openbiliclaw start` that periodically checks whether event-driven, trending, or explore refreshes should run. Let the extension service worker poll a pending-notification endpoint and display at most one high-confidence recommendation notification.

**Tech Stack:** FastAPI, asyncio, SQLite, Typer, Chrome extension service worker, TypeScript

---

### Task 1: Add runtime refresh state persistence

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_memory_manager.py`

**Step 1: Write the failing test**

Add tests for loading and saving `discovery_runtime.json`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_manager.py -q`

**Step 3: Write minimal implementation**

Add:
- `load_discovery_runtime_state()`
- `save_discovery_runtime_state()`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_manager.py -q`

**Step 5: Commit**

```bash
git add src/openbiliclaw/memory/manager.py tests/test_memory_manager.py
git commit -m "feat: persist discovery runtime state"
```

### Task 2: Extend content cache metadata for freshness and notifications

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**

Add tests for:
- `last_scored_at`
- `notification_sent`
- selecting notification candidates

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage.py -q`

**Step 3: Write minimal implementation**

Add migration/column support and helper methods:
- `mark_notification_sent()`
- `get_notification_candidate()`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_storage.py -q`

**Step 5: Commit**

```bash
git add src/openbiliclaw/storage/database.py tests/test_storage.py
git commit -m "feat: track cache freshness and notification state"
```

### Task 3: Implement backend refresh controller

**Files:**
- Create: `src/openbiliclaw/runtime/refresh.py`
- Test: `tests/test_refresh_runtime.py`

**Step 1: Write the failing test**

Add tests covering:
- event-triggered refresh
- trending refresh cadence
- explore refresh cadence
- no refresh below threshold

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_refresh_runtime.py -q`

**Step 3: Write minimal implementation**

Create a runtime helper that:
- reads runtime state
- counts pending strong-signal events
- decides which strategies to run
- runs discovery + recommendation refresh
- updates runtime state

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_refresh_runtime.py -q`

**Step 5: Commit**

```bash
git add src/openbiliclaw/runtime/refresh.py tests/test_refresh_runtime.py
git commit -m "feat: add continuous refresh controller"
```

### Task 4: Add runtime-status and pending-notification API endpoints

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_app.py`

**Step 1: Write the failing test**

Add tests for:
- `GET /api/runtime-status`
- `GET /api/notifications/pending`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_app.py -q`

**Step 3: Write minimal implementation**

Expose:
- runtime status payload
- single pending notification payload

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_app.py -q`

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "feat: expose runtime status and pending notifications"
```

### Task 5: Start background refresh loop with `openbiliclaw start`

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

Add a CLI/API lifecycle test ensuring the app starts with refresh support.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -q`

**Step 3: Write minimal implementation**

Attach a FastAPI startup background task that periodically checks refresh conditions.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -q`

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/cli.py tests/test_cli.py
git commit -m "feat: run background refresh loop in api server"
```

### Task 6: Add extension notification polling

**Files:**
- Modify: `extension/src/background/service-worker.ts`
- Test: `extension/tests/service-worker-buffer.test.ts`

**Step 1: Write the failing test**

Add tests for:
- polling pending notifications after flush
- creating a single notification

**Step 2: Run test to verify it fails**

Run: `npm test -- service-worker-buffer.test.ts`

**Step 3: Write minimal implementation**

After flushing events:
- fetch `/api/notifications/pending`
- call `chrome.notifications.create(...)`
- open video on click

**Step 4: Run test to verify it passes**

Run: `npm test -- service-worker-buffer.test.ts`

**Step 5: Commit**

```bash
git add extension/src/background/service-worker.ts extension/tests/service-worker-buffer.test.ts
git commit -m "feat: notify high-confidence recommendations"
```

### Task 7: Add popup runtime-status state handling

**Files:**
- Modify: `extension/popup/popup-api.js`
- Modify: `extension/popup/popup-helpers.js`
- Modify: `extension/popup/popup.js`
- Test: `extension/tests/popup-helpers.test.ts`

**Step 1: Write the failing test**

Add tests for:
- uninitialized state
- observing/refreshing state
- ready-with-recommendations state

**Step 2: Run test to verify it fails**

Run: `npm test -- popup-helpers.test.ts`

**Step 3: Write minimal implementation**

Fetch `runtime-status` and update popup hint text / state copy.

**Step 4: Run test to verify it passes**

Run: `npm test -- popup-helpers.test.ts`

**Step 5: Commit**

```bash
git add extension/popup/popup-api.js extension/popup/popup-helpers.js extension/popup/popup.js extension/tests/popup-helpers.test.ts
git commit -m "feat: show runtime refresh state in popup"
```

### Task 8: Add integration coverage and docs

**Files:**
- Modify: `tests/test_e2e_flow_integration.py`
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/manual-e2e.md`

**Step 1: Write the failing integration test**

Cover:
- event ingestion
- refresh trigger
- recommendation generation
- notification selection

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_e2e_flow_integration.py -q`

**Step 3: Write minimal implementation/docs**

Update docs and integration assertions.

**Step 4: Run full verification**

Run:

```bash
ruff check src/ tests/
mypy src/
pytest -q
cd extension && npm test && npm run typecheck && npm run build
```

Expected:
- all Python checks pass
- all extension checks pass

**Step 5: Commit**

```bash
git add tests/test_e2e_flow_integration.py docs/modules/extension.md docs/modules/recommendation.md docs/modules/cli.md docs/changelog.md docs/v0.1-todolist.md docs/manual-e2e.md
git commit -m "test: verify continuous refresh and notification flow"
```
