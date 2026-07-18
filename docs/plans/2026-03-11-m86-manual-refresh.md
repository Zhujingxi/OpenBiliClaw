# Popup 立即刷新推荐 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a popup button that triggers one explicit backend refresh cycle and reloads the recommendation list.

**Architecture:** Add a small backend endpoint that delegates to the existing runtime refresh controller, then wire popup UI to call it and reload runtime status plus recommendations. Keep refresh orchestration in Python; keep popup as a thin client with loading/error states.

**Tech Stack:** FastAPI, existing runtime refresh controller, browser extension popup JS, Node test runner, pytest

---

### Task 1: Add backend refresh endpoint

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Test: `tests/test_api_app.py`

**Step 1: Write the failing test**

Add pytest coverage for:
- `POST /api/recommendations/refresh` returns `ok=true`, `refreshed=true`, strategies, recommendation count
- uninitialized runtime returns `refreshed=false`, `reason="not_initialized"`

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_api_app.py -q`
Expected: FAIL because endpoint does not exist

**Step 3: Write minimal implementation**

- Add response model for refresh result
- Add route in `create_app()`
- Delegate to `runtime_controller.refresh_if_needed()` or equivalent
- Return normalized JSON shape

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_api_app.py -q`
Expected: PASS

### Task 2: Add popup refresh API helper

**Files:**
- Modify: `extension/popup/popup-api.js`
- Test: `extension/tests/popup-helpers.test.ts` or new popup API test file

**Step 1: Write the failing test**

Add test coverage for a helper that calls `POST /recommendations/refresh`.

**Step 2: Run test to verify it fails**

Run: `npm test -- --runInBand`
Expected: FAIL because helper is missing

**Step 3: Write minimal implementation**

- Export `refreshRecommendations()` from `popup-api.js`
- Reuse existing `requestJson()`

**Step 4: Run test to verify it passes**

Run: `npm test -- --runInBand`
Expected: PASS

### Task 3: Add popup button and loading state

**Files:**
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Test: `extension/tests/popup-layout.test.ts`
- Test: `extension/tests/popup-copy.test.ts`

**Step 1: Write the failing test**

Add tests that assert:
- popup contains an `立即刷新` button in recommend view
- loading copy appears during refresh

**Step 2: Run test to verify it fails**

Run: `npm test -- --runInBand`
Expected: FAIL because button/copy are missing

**Step 3: Write minimal implementation**

- Add button markup
- Add click handler in `popup.js`
- On click:
  - disable button
  - call `refreshRecommendations()`
  - reload runtime status and recommendations
  - restore button state

**Step 4: Run test to verify it passes**

Run: `npm test -- --runInBand`
Expected: PASS

### Task 4: Handle failure and uninitialized states

**Files:**
- Modify: `extension/popup/popup.js`
- Test: `extension/tests/popup-helpers.test.ts` or popup behavior test file

**Step 1: Write the failing test**

Cover:
- uninitialized refresh response shows `先执行 openbiliclaw init`
- failed refresh keeps old list and shows retry hint

**Step 2: Run test to verify it fails**

Run: `npm test -- --runInBand`
Expected: FAIL because failure handling is incomplete

**Step 3: Write minimal implementation**

- Map backend `reason`
- Keep existing recommendations on failure
- Update hint text only

**Step 4: Run test to verify it passes**

Run: `npm test -- --runInBand`
Expected: PASS

### Task 5: Update docs and verify full suite

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`
- Modify: `docs/v0.1-todolist.md`

**Step 1: Update docs**

- Document popup manual refresh button
- Add changelog entry
- Mark the popup refresh capability in todo/status notes

**Step 2: Run verification**

Run:
- `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/`
- `PYTHONPATH=src .venv/bin/python -m mypy src/`
- `PYTHONPATH=src .venv/bin/pytest -q`
- `cd extension && npm test -- --runInBand`
- `cd extension && npm run typecheck`
- `cd extension && npm run build`

Expected: all pass

**Step 3: Commit**

```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py tests/test_api_app.py extension/popup/popup-api.js extension/popup/popup.html extension/popup/popup.js extension/tests/*.test.ts docs/modules/extension.md docs/changelog.md docs/v0.1-todolist.md docs/plans/2026-03-11-m86-manual-refresh-design.md docs/plans/2026-03-11-m86-manual-refresh.md
git commit -m "feat: add manual popup recommendation refresh"
```
