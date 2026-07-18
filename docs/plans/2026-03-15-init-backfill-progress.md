# Init Backfill Progress Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add visible staged backfill progress output to `openbiliclaw init` while preserving the existing pool-target behavior.

**Architecture:** Extend the init backfill helper to emit small progress messages before and after each stage. Keep strategy order, target count, and failure handling unchanged, and lock behavior with CLI tests.

**Tech Stack:** Python, Typer CLI, Rich console output, pytest

---

### Task 1: Write failing CLI output tests

**Files:**
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

Add a CLI test that expects output like:

```python
assert "补货阶段 1/3" in result.stdout
assert "当前池子 0/50" in result.stdout
assert "阶段完成" in result.stdout
```

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -k "init and progress" -v`

Expected: FAIL because the current helper does not print stage progress.

**Step 3: Write minimal implementation**

No implementation in this task.

**Step 4: Re-run the same test**

Confirm the failure is specifically about missing progress output.

**Step 5: Commit**

Do not commit yet.

### Task 2: Implement progress output

**Files:**
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Add helper formatting**

Add small helpers or inline formatting for:

- stage label
- current pool count / target count
- requested backfill limit

**Step 2: Print progress around each stage**

Before each stage:

```python
console.print(f"补货阶段 {index}/{total}: ...")
```

After each stage:

```python
console.print(f"阶段完成: 当前池子 {pool}/{target}")
```

**Step 3: Run targeted tests**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -k "init and progress" -v`

Expected: PASS

**Step 4: Run broader CLI tests**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -v`

Expected: PASS

**Step 5: Commit**

Do not commit yet.

### Task 3: Update docs

**Files:**
- Modify: `README.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`

**Step 1: Document visible staged progress**

Add concise notes that `init` will print staged backfill progress while filling the first-run pool.

**Step 2: Run lint for touched files**

Run: `./.venv/bin/ruff check src/openbiliclaw/cli.py tests/test_cli.py`

Expected: PASS

**Step 3: Commit**

Do not commit yet.
