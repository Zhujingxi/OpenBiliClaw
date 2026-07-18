# M93 Chat Learning Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make CLI and popup chat sessions produce controlled learning signals that can eventually update preferences and the soul profile.

**Architecture:** Persist each chat turn as a `dialogue` event, extract structured `insight_candidate` records into JSON, merge them over time, and only feed high-confidence repeated candidates into the existing preference/profile refresh path. Keep the first version backend-only with no new confirmation UI.

**Tech Stack:** Python, Typer CLI, FastAPI, Rich, SQLite, JSON memory files, pytest

---

### Task 1: Add failing tests for runtime chat-learning data flow

**Files:**
- Modify: `tests/test_memory_manager.py`
- Modify: `tests/test_soul_engine.py`
- Modify: `tests/test_api_app.py`

**Step 1: Write the failing tests**

Add tests for:
- saving/loading `insight_candidates.json`
- merging repeated candidates
- chat route or dialogue path recording `dialogue` events
- thresholded candidate processing triggering preference refresh

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_memory_manager.py tests/test_soul_engine.py tests/test_api_app.py`
Expected: FAIL because candidate storage / processing APIs do not exist yet.

**Step 3: Commit**

Do not commit red tests yet.

### Task 2: Implement insight-candidate storage in MemoryManager

**Files:**
- Modify: `src/openbiliclaw/memory/manager.py`
- Test: `tests/test_memory_manager.py`

**Step 1: Write minimal implementation**

Add:
- `load_insight_candidates()`
- `save_insight_candidates(items)`
- `merge_insight_candidates(items)`

Use `data/memory/insight_candidates.json`.

**Step 2: Run focused tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_memory_manager.py`
Expected: PASS

**Step 3: Commit**

```bash
git add src/openbiliclaw/memory/manager.py tests/test_memory_manager.py
git commit -m "feat: persist dialogue insight candidates"
```

### Task 3: Add DialogueInsightAnalyzer with TDD

**Files:**
- Create: `src/openbiliclaw/soul/dialogue_insight_analyzer.py`
- Test: `tests/test_dialogue_insight_analyzer.py`

**Step 1: Write the failing test**

Add tests for:
- valid structured extraction
- bad JSON returns safe empty result or raises controlled error
- confidence normalization

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_dialogue_insight_analyzer.py`
Expected: FAIL because file/class does not exist.

**Step 3: Write minimal implementation**

Implement analyzer using the existing LLM service structured-task path.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_dialogue_insight_analyzer.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/dialogue_insight_analyzer.py tests/test_dialogue_insight_analyzer.py
git commit -m "feat: add dialogue insight analyzer"
```

### Task 4: Wire SocraticDialogue to persist dialogue events and candidates

**Files:**
- Modify: `src/openbiliclaw/soul/dialogue.py`
- Modify: `tests/test_soul_dialogue.py` or create it if missing

**Step 1: Write the failing test**

Test that after `respond()`:
- a `dialogue` event is written
- analyzer is called
- candidate merge is attempted

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_soul_dialogue.py`
Expected: FAIL because the chat path does not persist learning signals yet.

**Step 3: Write minimal implementation**

Inject memory/analyzer dependencies or derive them from the existing soul engine path.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_soul_dialogue.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/dialogue.py tests/test_soul_dialogue.py
git commit -m "feat: persist dialogue learning signals"
```

### Task 5: Add thresholded candidate processing in SoulEngine

**Files:**
- Modify: `src/openbiliclaw/soul/engine.py`
- Modify: `tests/test_soul_engine.py`

**Step 1: Write the failing test**

Cover:
- below-threshold candidates do nothing
- repeated/high-confidence candidates trigger preference refresh
- significant change triggers profile rebuild

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_soul_engine.py`
Expected: FAIL because chat candidates are not part of the refresh path.

**Step 3: Write minimal implementation**

Add a `process_dialogue_insights_if_needed()`-style entrypoint and reuse the existing preference/profile update path.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_soul_engine.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/soul/engine.py tests/test_soul_engine.py
git commit -m "feat: refresh profile from dialogue insights"
```

### Task 6: Connect popup/API chat path to the new learning flow

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `tests/test_api_app.py`

**Step 1: Write or extend the failing test**

Verify `/api/chat` still returns a reply and now also triggers the dialogue learning path.

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_api_app.py`
Expected: FAIL because no learning trigger is asserted yet.

**Step 3: Write minimal implementation**

Keep the route contract unchanged; only extend internal behavior.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_api_app.py`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/api/app.py tests/test_api_app.py
git commit -m "feat: route popup chat into learning flow"
```

### Task 7: Update docs and run full verification

**Files:**
- Modify: `docs/modules/soul.md`
- Modify: `docs/modules/memory.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`
- Modify: `docs/v0.1-todolist.md`

**Step 1: Update docs**

Document:
- `dialogue` events
- `insight_candidates.json`
- thresholded chat-driven profile refresh

**Step 2: Run full verification**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/
PYTHONPATH=src .venv/bin/python -m mypy src/
PYTHONPATH=src .venv/bin/python -m pytest -q
cd extension && npm test && npm run typecheck && npm run build
```

Expected: all green

**Step 3: Commit**

```bash
git add docs/modules/soul.md docs/modules/memory.md docs/modules/cli.md docs/modules/extension.md docs/changelog.md docs/v0.1-todolist.md
git commit -m "docs: document chat-driven learning flow"
```
