# Runtime Pool Target 300 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Raise the steady-state discovery-pool target to 300 while keeping first-run init backfill at 100.

**Architecture:** Keep the existing staged init backfill flow and runtime refresh guardrails, but split their targets: `init` uses a smaller cold-start pool floor, while runtime refresh and config defaults use a larger steady-state target.

**Tech Stack:** Python, Typer, pytest, TOML docs

---

### Task 1: Lock the new defaults with failing tests

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

- Change the config default assertions from `150` to `300`
- Change the init backfill assertions from `50` to `100`

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/test_config.py tests/test_cli.py -k "pool_target_count or init_backfills_pool_in_stages_until_target_is_reached or init_stops_backfill_early_when_first_stage_reaches_pool_target" -v`

**Step 3: Write minimal implementation**

- Update runtime config defaults
- Update init pool target constants/defaults

**Step 4: Run test to verify it passes**

Run the same pytest command.

**Step 5: Commit**

```bash
git add tests/test_config.py tests/test_cli.py src/openbiliclaw/config.py src/openbiliclaw/runtime/refresh.py src/openbiliclaw/cli.py config.example.toml
git commit -m "feat: raise runtime pool target defaults"
```

### Task 2: Update docs for the split pool targets

**Files:**
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

- Document runtime default target `300`
- Document first-run init target `100`
- Update any CLI example output that still shows `0/50`, `28/50`, or “至少 50 条”

**Step 2: Run doc sanity checks**

Run: `git diff --check -- docs/modules/config.md docs/modules/cli.md docs/changelog.md`

**Step 3: Commit**

```bash
git add docs/modules/config.md docs/modules/cli.md docs/changelog.md
git commit -m "docs: describe updated pool targets"
```

### Task 3: Final verification

**Files:**
- Verify the files above plus any touched config/runtime files

**Step 1: Run targeted verification**

Run:

```bash
./.venv/bin/pytest tests/test_config.py tests/test_cli.py -k "pool_target_count or init_backfills_pool_in_stages_until_target_is_reached or init_stops_backfill_early_when_first_stage_reaches_pool_target" -v
./.venv/bin/ruff check src/ tests/
```

**Step 2: Run full verification if targeted checks pass**

Run:

```bash
./.venv/bin/pytest
```

**Step 3: Inspect diff**

Run: `git diff --stat`
