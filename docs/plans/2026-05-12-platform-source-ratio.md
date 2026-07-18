# Platform Source Ratio Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the recommendation discovery pool enforce a configurable platform ratio, defaulting to Bilibili / Xiaohongshu / Douyin = 8 / 1 / 1.

**Architecture:** Keep source-family accounting in `runtime.refresh` and `storage.database`, but move the share definition into scheduler config. Runtime replenishment will compute platform quotas, protect under-quota platform rows during trim/reactivation, and trigger Douyin discovery when the Douyin platform family is under quota. Bilibili remains backed by the existing discovery engine; Xiaohongshu remains backed by `xhs_producer`.

**Tech Stack:** Python dataclasses, Typer config display, SQLite-backed pool metadata, pytest.

---

### Task 1: Configurable Platform Shares

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `config.example.toml`
- Test: `tests/test_config.py`

**Steps:**
1. Add a failing config test asserting `scheduler.pool_source_shares == {"bilibili": 8, "xiaohongshu": 1, "douyin": 1}` by default.
2. Add a failing config override test for `[scheduler.pool_source_shares]`.
3. Add `pool_source_shares` to `SchedulerConfig`, TOML parsing, and config rendering.
4. Update `config.example.toml`.
5. Run `uv run pytest tests/test_config.py::test_scheduler_pool_source_shares_defaults tests/test_config.py::test_scheduler_pool_source_shares_override -q`.

### Task 2: Runtime Quotas And Replenishment

**Files:**
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Test: `tests/test_refresh_runtime.py`

**Steps:**
1. Add failing tests that `pool_target_count=600` maps to `bilibili=480`, `xiaohongshu=60`, `douyin=60`.
2. Add failing tests that Bilibili source deficits map back to Bilibili strategies, and Douyin deficits call a Douyin producer.
3. Add a `douyin_producer` dependency to `ContinuousRefreshController`.
4. Use configured platform shares for `_source_target_counts()`.
5. Add `_tick_douyin_producer()` and call it when Douyin is under quota.
6. Run targeted refresh runtime tests.

### Task 3: Runtime Bootstrap Wiring

**Files:**
- Modify: runtime bootstrap / API context files that instantiate `ContinuousRefreshController`.
- Test: existing runtime/bootstrap tests if present.

**Steps:**
1. Find every `ContinuousRefreshController(...)` construction.
2. Pass `pool_source_shares=config.scheduler.pool_source_shares`.
3. Build a small Douyin producer wrapper that invokes `DouyinDiscoveryService(cache=True)` when enabled and Cookie is available.
4. Keep failure soft: log and return no-op if Douyin auth/config is unavailable.

### Task 4: Docs And Verification

**Files:**
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/changelog.md`

**Steps:**
1. Document `[scheduler.pool_source_shares]` and the 8:1:1 default.
2. Document runtime replenishment and trim semantics.
3. Run targeted tests, Ruff, and a real smoke for `discover-douyin`.
