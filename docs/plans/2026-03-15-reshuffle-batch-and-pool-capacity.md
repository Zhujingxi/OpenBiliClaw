# Recommendation Reshuffle Batch And Pool Capacity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make popup "换一批" return up to 10 recommendations, raise the default discovery-pool target to 150, and fix reshuffle underfilling when top candidates share the same style.

**Architecture:** Keep the existing popup and API flow intact, but change the backend defaults that drive reshuffle volume and pool replenishment. Fix the real underfill bug in `RecommendationEngine` by preserving diversity preference without letting one dominant `style_key` cap the batch below the requested size when enough candidates still exist.

**Tech Stack:** Python, FastAPI, SQLite, pytest, Node test runner, Markdown docs

---

### Task 1: Lock the reshuffle underfill bug with a failing engine test

**Files:**
- Modify: `tests/test_recommendation_engine.py`
- Modify: `src/openbiliclaw/recommendation/engine.py`

**Step 1: Write the failing test**

Add a test that seeds more than five pool candidates where the top-ranked rows all share one `style_key`, but enough fresh candidates exist to fill the requested batch. Assert that `reshuffle_recommendations(limit=5)` still returns 5 items instead of stopping early at 2-4.

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_recommendation_engine.py::test_reshuffle_recommendations_backfills_to_requested_limit_when_style_is_dominant -q`

Expected: FAIL because the current style cap prevents filling the whole batch.

**Step 3: Write minimal implementation**

Update `_select_diversified_batch()` so the first pass still prefers topic/style diversity, but the backfill phase can relax style caps enough to reach `limit` when enough ranked candidates remain.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_recommendation_engine.py::test_reshuffle_recommendations_backfills_to_requested_limit_when_style_is_dominant -q`

Expected: PASS

### Task 2: Raise reshuffle batch size to 10 with API coverage

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `tests/test_api_app.py`

**Step 1: Write the failing test**

Change the reshuffle endpoint test to expect `limit == 10` in the fake recommendation engine.

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_api_app.py::TestBackendAPI::test_reshuffle_recommendations_endpoint_returns_immediate_items -q`

Expected: FAIL because the route still passes `5`.

**Step 3: Write minimal implementation**

Update `/api/recommendations/reshuffle` to call `recommendation_engine.reshuffle_recommendations(..., limit=10)`.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_api_app.py::TestBackendAPI::test_reshuffle_recommendations_endpoint_returns_immediate_items -q`

Expected: PASS

### Task 3: Raise default pool target to 150 and document it

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `config.example.toml`
- Modify: `tests/test_config.py`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/changelog.md`

**Step 1: Write the failing test**

Add a config test that asserts `Config().scheduler.pool_target_count == 150` and update the existing scheduler build test to use the new default-oriented examples.

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_config.py::test_config_defaults_pool_target_count_to_150 -q`

Expected: FAIL because the current default is `30`.

**Step 3: Write minimal implementation**

Raise the default `pool_target_count` to `150` in both runtime config dataclasses and `config.example.toml`, then update docs to describe the new default and the fact that popup reshuffle now aims for up to 10 items per batch.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_config.py::test_config_defaults_pool_target_count_to_150 -q`

Expected: PASS

### Task 4: Run focused verification

**Files:**
- Verify only

**Step 1: Run Python verification**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_recommendation_engine.py tests/test_api_app.py tests/test_config.py -q`

Expected: PASS

**Step 2: Run popup verification**

Run: `cd extension && npm test -- popup-api.test.ts popup-helpers.test.ts popup-layout.test.ts chat-layout.test.ts`

Expected: PASS

**Step 3: Commit**

```bash
git add src/openbiliclaw/recommendation/engine.py src/openbiliclaw/api/app.py src/openbiliclaw/config.py config.example.toml tests/test_recommendation_engine.py tests/test_api_app.py tests/test_config.py docs/modules/config.md docs/modules/recommendation.md docs/changelog.md docs/plans/2026-03-15-reshuffle-batch-and-pool-capacity.md
git commit -m "fix: expand reshuffle batches and pool defaults"
```
