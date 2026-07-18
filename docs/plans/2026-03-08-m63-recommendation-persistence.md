# Recommendation Persistence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add structured recommendation feedback persistence, CLI feedback command, and feedback-event logging.

**Architecture:** Extend the existing `recommendations` table in place, keep a single current feedback state per recommendation, and wire CLI feedback submission through `RecommendationEngine` plus `MemoryManager` event logging. This keeps `6.3` lightweight while forming a complete recommendation lifecycle.

**Tech Stack:** Python 3.11, Typer, SQLite, Pytest

---

### Task 1: Add failing storage tests for recommendation feedback persistence

**Files:**
- Modify: `tests/test_storage.py`
- Modify: `src/openbiliclaw/storage/database.py`

**Step 1: Write the failing tests**

Add tests for:
- `get_recommendation_by_id()`
- `update_recommendation_feedback()`
- persisted `feedback_type` / `feedback_note` / `feedback_at`

**Step 2: Run tests to verify failure**

Run: `PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_storage.py -q`

Expected: failure due to missing methods or missing columns.

**Step 3: Write minimal implementation**

- Add idempotent migration logic to `Database.initialize()`
- Add `get_recommendation_by_id()`
- Add `update_recommendation_feedback()`

**Step 4: Run tests to verify pass**

Run the same command and confirm green.

**Step 5: Commit**

```bash
git add tests/test_storage.py src/openbiliclaw/storage/database.py
git commit -m "feat: persist recommendation feedback fields"
```

### Task 2: Add failing recommendation engine tests for feedback update entrypoint

**Files:**
- Modify: `tests/test_recommendation_engine.py`
- Modify: `src/openbiliclaw/recommendation/engine.py`

**Step 1: Write the failing test**

Add a test for `record_feedback()` verifying that:
- the target record is updated
- note and feedback type are persisted

**Step 2: Run test to verify failure**

Run: `PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_recommendation_engine.py -q`

Expected: failure because `record_feedback()` does not exist.

**Step 3: Write minimal implementation**

- Add `RecommendationEngine.record_feedback()`
- Delegate to `Database.update_recommendation_feedback()`

**Step 4: Run test to verify pass**

Run the same command and confirm green.

**Step 5: Commit**

```bash
git add tests/test_recommendation_engine.py src/openbiliclaw/recommendation/engine.py
git commit -m "feat: add recommendation feedback update flow"
```

### Task 3: Add failing CLI tests for `openbiliclaw feedback`

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Write the failing tests**

Add tests for:
- `openbiliclaw feedback 7 like`
- `openbiliclaw feedback 7 dislike --note "太浅了"`
- invalid/nonexistent recommendation id exits with clear message

Also verify that successful CLI feedback writes a `feedback` event through `MemoryManager`.

**Step 2: Run tests to verify failure**

Run: `PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -q`

Expected: failure because the CLI command does not exist yet.

**Step 3: Write minimal implementation**

- Add `_build_memory_manager()` helper if needed
- Add `feedback` command to `cli.py`
- Validate recommendation existence
- Call engine feedback update
- Write feedback event

**Step 4: Run test to verify pass**

Run the same command and confirm green.

**Step 5: Commit**

```bash
git add tests/test_cli.py src/openbiliclaw/cli.py
git commit -m "feat: add recommendation feedback cli"
```

### Task 4: Update docs and run full verification

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

- Mark `6.3` complete in todo if implementation meets scope
- Update recommendation module guide with feedback persistence fields
- Update CLI docs with `feedback` command
- Append changelog entry

**Step 2: Run full verification**

Run:

```bash
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q
```

Expected:
- Ruff clean
- MyPy clean
- Full test suite green

**Step 3: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/recommendation.md docs/modules/cli.md docs/changelog.md
git commit -m "docs: update recommendation persistence status"
```
