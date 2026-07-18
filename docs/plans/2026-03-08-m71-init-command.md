# Init Command Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `openbiliclaw init` to bootstrap history import, preference analysis, soul profile generation, and one initial discovery pass.

**Architecture:** Keep orchestration in `cli.py`, add small builder helpers for auth/API/discovery, and reuse existing `MemoryManager`, `SoulEngine`, and `ContentDiscoveryEngine` instead of inventing a new runtime layer.

**Tech Stack:** Python 3.11, Typer, SQLite, Pytest, existing Bilibili API client and discovery strategies

---

### Task 1: Add failing CLI tests for `openbiliclaw init`

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Write the failing tests**

Add tests for:
- auth invalid -> exits with clear message
- empty history -> exits with guidance
- success path -> imports history, analyzes events, builds profile, runs discovery
- discovery failure -> reports partial success without discarding profile generation

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -q`

Expected: fail because `init` command does not exist.

**Step 3: Write minimal implementation**

- Add `init` command
- Add auth/history/profile/discovery phase output

**Step 4: Run test to verify it passes**

Run the same command and confirm green.

**Step 5: Commit**

```bash
git add tests/test_cli.py src/openbiliclaw/cli.py
git commit -m "feat: add init command orchestration"
```

### Task 2: Add helper builders and history mapping

**Files:**
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Add minimal helpers**

- `_build_bilibili_client()`
- `_build_discovery_engine()`
- `_history_item_to_event()`

**Step 2: Keep scope tight**

- Reuse existing strategies only
- Do not move orchestration into a new engine class

**Step 3: Verify**

Run: `PYTHONPATH=src /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py -q`

Expected: helper-backed CLI tests pass.

**Step 4: Commit**

```bash
git add src/openbiliclaw/cli.py
git commit -m "feat: wire init helpers for bilibili and discovery"
```

### Task 3: Update docs and run full verification

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

- Mark `openbiliclaw init` as implemented
- Document CLI behavior and partial-success semantics
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
- Full suite green

**Step 3: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/cli.md docs/changelog.md
git commit -m "docs: update init command status"
```
