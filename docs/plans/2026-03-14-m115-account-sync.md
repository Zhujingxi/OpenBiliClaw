# 账户侧定时同步 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a low-frequency account sync loop that periodically imports Bilibili history, favorites, and following into the event layer after initialization.

**Architecture:** Introduce an account sync state file and a lightweight runtime sync service. The service runs alongside the existing refresh loop, performs incremental account fetches, maps new items to events, persists sync state, and reuses `SoulEngine.analyze_events()` for preference/profile updates.

**Tech Stack:** Existing `BilibiliAPIClient`, `MemoryManager`, `SoulEngine`, FastAPI runtime app, JSON state files.

---

### Task 1: Add account sync state persistence

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_memory_manager.py`

**Step 1: Write the failing test**

Add tests that verify:
- `load_account_sync_state()` returns defaults when file is absent
- `save_account_sync_state()` persists and reloads fields correctly

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_memory_manager.py -q`
Expected: FAIL because account sync state helpers do not exist yet.

**Step 3: Write minimal implementation**

Add:
- `load_account_sync_state()`
- `save_account_sync_state()`

Persist to:
- `data/memory/account_sync_state.json`

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_memory_manager.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/memory/manager.py tests/test_memory_manager.py
git commit -m "feat: persist account sync state"
```

### Task 2: Implement incremental account sync service

**Files:**
- Create: `src/openbiliclaw/runtime/account_sync.py`
- Test: `tests/test_account_sync.py`

**Step 1: Write the failing test**

Add tests that verify:
- recent history items are filtered incrementally
- favorite/following signatures suppress duplicate imports
- new items are mapped to `view` / `favorite` / `follow` events
- sync returns partial success when one source fails

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_account_sync.py -q`
Expected: FAIL because service does not exist yet.

**Step 3: Write minimal implementation**

Create an `AccountSyncService` that:
- loads sync state
- fetches history / favorites / following
- performs incremental diffing
- writes new events through `MemoryManager`
- calls `SoulEngine.analyze_events()` only when there are new events
- stores updated sync state

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_account_sync.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/runtime/account_sync.py tests/test_account_sync.py
git commit -m "feat: add incremental account sync service"
```

### Task 3: Run account sync in backend runtime

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/runtime/__init__.py`
- Test: `tests/test_api_app.py`

**Step 1: Write the failing test**

Add tests that verify:
- startup creates the account sync loop when service is available
- `runtime-status` includes `last_account_sync_at` and `last_account_sync_error`

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_api_app.py -q`
Expected: FAIL because runtime status lacks account sync fields.

**Step 3: Write minimal implementation**

Wire `AccountSyncService` into `create_app()`:
- create one low-frequency background loop
- check whether sync is due
- execute sync without blocking API
- enrich `runtime-status`

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_api_app.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/runtime/__init__.py tests/test_api_app.py
git commit -m "feat: run periodic account sync in backend"
```

### Task 4: Verify integration with soul analysis

**Files:**
- Test: `tests/test_soul_engine.py`
- Test: `tests/test_account_sync.py`

**Step 1: Write the failing test**

Add or extend tests that verify:
- newly imported events call `SoulEngine.analyze_events()`
- no-op sync does not reanalyze
- partial sync still analyzes successfully imported events

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_account_sync.py tests/test_soul_engine.py -q`
Expected: FAIL until integration logic is complete.

**Step 3: Write minimal implementation**

Complete the service flow and ensure imported account-side events reuse the existing event analysis chain.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_account_sync.py tests/test_soul_engine.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_account_sync.py tests/test_soul_engine.py
git commit -m "test: cover account sync event analysis"
```

### Task 5: Update docs and run full verification

**Files:**
- Modify: `docs/modules/bilibili.md`
- Modify: `docs/modules/memory.md`
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`
- Modify: `docs/v0.1-todolist.md`

**Step 1: Update docs**

Document:
- account-side periodic sync scope
- supported sources: history / favorites / following
- sync state file
- runtime status fields

**Step 2: Run verification**

Run:
- `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/`
- `PYTHONPATH=src .venv/bin/python -m mypy src/`
- `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected:
- PASS

**Step 3: Commit**

```bash
git add docs/modules/bilibili.md docs/modules/memory.md docs/modules/soul.md docs/modules/cli.md docs/changelog.md docs/v0.1-todolist.md
git commit -m "docs: document periodic account sync"
```
