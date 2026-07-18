# 运行时实时状态流 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a websocket runtime stream so the popup can show live pool/refill state while the backend is observing, refreshing, and reshuffling.

**Architecture:** Introduce a lightweight runtime event hub in Python, expose it through `/api/runtime-stream`, publish refresh lifecycle events from the continuous refresh controller, and let popup subscribe with websocket while keeping existing REST status as fallback.

**Tech Stack:** FastAPI WebSocket, asyncio, existing runtime refresh controller, extension popup JavaScript.

---

### Task 1: Add runtime event hub

**Files:**
- Create: `src/openbiliclaw/runtime/events.py`
- Test: `tests/test_runtime_events.py`

**Step 1: Write the failing test**

Add tests that verify:
- subscribers receive published events
- disconnected subscribers are removed safely

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_runtime_events.py -q`
Expected: FAIL because module does not exist yet.

**Step 3: Write minimal implementation**

Create a small `RuntimeEventHub` with:
- `subscribe()`
- `unsubscribe()`
- `publish(event)`

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_runtime_events.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/runtime/events.py tests/test_runtime_events.py
git commit -m "feat: add runtime event hub"
```

### Task 2: Expose websocket runtime stream

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/api/models.py`
- Test: `tests/test_api_app.py`

**Step 1: Write the failing test**

Add API tests that:
- connect to `/api/runtime-stream`
- receive one published event

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_api_app.py -q`
Expected: FAIL because websocket route does not exist.

**Step 3: Write minimal implementation**

Wire `RuntimeEventHub` into `create_app()` and add websocket route:
- accept connection
- subscribe client queue
- send JSON payloads
- unsubscribe on disconnect

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_api_app.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py tests/test_api_app.py
git commit -m "feat: expose runtime websocket stream"
```

### Task 3: Publish refresh lifecycle events

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Test: `tests/test_refresh_runtime.py`

**Step 1: Write the failing test**

Add tests that verify controller publishes:
- refresh started
- strategy stage messages
- pool updated
- reshuffle/failed states where applicable

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_refresh_runtime.py -q`
Expected: FAIL because no events are published yet.

**Step 3: Write minimal implementation**

Inject event hub into controller and publish structured events at:
- refresh start
- each strategy stage
- pool updated
- manual refresh success/failure

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_refresh_runtime.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/runtime/refresh.py tests/test_refresh_runtime.py
git commit -m "feat: publish runtime refresh events"
```

### Task 4: Add popup websocket client

**Files:**
- Create: `extension/popup/popup-stream.js`
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-helpers.js`
- Test: `extension/tests/popup-stream.test.ts`
- Test: `extension/tests/popup-helpers.test.ts`

**Step 1: Write the failing test**

Add tests for:
- websocket event normalization
- popup state updates from stream payloads

**Step 2: Run test to verify it fails**

Run: `npm test -- --runInBand popup-stream.test.ts popup-helpers.test.ts`
Expected: FAIL because stream module/helpers do not exist yet.

**Step 3: Write minimal implementation**

Implement a popup websocket client that:
- connects to `/api/runtime-stream`
- reconnects with backoff
- updates footer hint and pool summary on incoming events
- falls back silently if ws unavailable

**Step 4: Run test to verify it passes**

Run: `npm test -- --runInBand popup-stream.test.ts popup-helpers.test.ts`
Expected: PASS

**Step 5: Commit**

```bash
git add extension/popup/popup-stream.js extension/popup/popup.js extension/popup/popup-helpers.js extension/tests/popup-stream.test.ts extension/tests/popup-helpers.test.ts
git commit -m "feat: stream runtime updates to popup"
```

### Task 5: Run extension verification and update docs

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`
- Modify: `docs/v0.1-todolist.md`

**Step 1: Update docs**

Document:
- websocket runtime stream
- realtime pool status in popup
- REST fallback behavior

**Step 2: Run verification**

Run:
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_runtime_events.py tests/test_api_app.py tests/test_refresh_runtime.py -q`
- `cd extension && npm test -- --runInBand popup-stream.test.ts popup-helpers.test.ts popup-layout.test.ts`
- `cd extension && npm run typecheck`
- `cd extension && npm run build`

Expected:
- PASS

**Step 3: Commit**

```bash
git add docs/modules/extension.md docs/changelog.md docs/v0.1-todolist.md
git commit -m "docs: document runtime stream updates"
```
