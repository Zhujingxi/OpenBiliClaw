# Pool Target Guardrails Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add safety guardrails for `scheduler.pool_target_count` so extreme values do not cause runaway discovery refresh cost.

**Architecture:** Keep the existing default pool target at `150`, but add two explicit safeguards: config validation caps `pool_target_count` to `1..300`, and runtime refresh caps each single discover backfill request at `60`. Tests and config docs must reflect both limits.

**Tech Stack:** Python, pytest, Markdown docs

---

### Task 1: Lock config validation with a failing test

**Files:**
- Modify: `tests/test_config.py`
- Modify: `src/openbiliclaw/config.py`

**Step 1: Write the failing test**

Add one test that constructs `Config(scheduler=...)` with `pool_target_count=301` and asserts `validate_runtime_config()` raises `ConfigError`.

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_config.py::test_validate_runtime_config_rejects_pool_target_count_above_cap -q`

Expected: FAIL because no range validation exists yet.

**Step 3: Write minimal implementation**

Extend config issue collection to reject values below `1` or above `300`.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_config.py::test_validate_runtime_config_rejects_pool_target_count_above_cap -q`

Expected: PASS

### Task 2: Lock single-refresh discovery cap with a failing test

**Files:**
- Modify: `tests/test_refresh_runtime.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`

**Step 1: Write the failing test**

Add a refresh-runtime test where `pool_target_count=300`, `pool_count=0`, and verify the first `discover()` call uses `60`, not the full gap.

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_refresh_runtime.py::test_refresh_controller_caps_single_discovery_backfill_request -q`

Expected: FAIL because the current code requests the full gap.

**Step 3: Write minimal implementation**

Introduce a single-refresh backfill cap of `60` and apply it when building `discover(..., limit=...)` inside `_run_refresh_plan()`.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_refresh_runtime.py::test_refresh_controller_caps_single_discovery_backfill_request -q`

Expected: PASS

### Task 3: Update docs for the new guardrails

**Files:**
- Modify: `config.example.toml`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`

**Step 1: Update config-facing docs**

Document that:
- default `pool_target_count` remains `150`
- allowed range is `1..300`
- runtime single-refresh backfill requests are capped at `60`

**Step 2: Verify changed docs are the minimum needed**

Run: `git diff -- config.example.toml docs/modules/config.md docs/changelog.md`

Expected: Only guardrail copy changes.

### Task 4: Run focused verification

**Files:**
- Verify only

**Step 1: Run Python verification**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_config.py tests/test_refresh_runtime.py -q`

Expected: PASS

**Step 2: Commit**

```bash
git add src/openbiliclaw/config.py src/openbiliclaw/runtime/refresh.py tests/test_config.py tests/test_refresh_runtime.py config.example.toml docs/modules/config.md docs/changelog.md docs/plans/2026-03-15-pool-target-guardrails-design.md docs/plans/2026-03-15-pool-target-guardrails.md
git commit -m "fix: add pool target guardrails"
```
