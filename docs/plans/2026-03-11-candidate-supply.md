# Candidate Supply Upgrade Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让发现链路在保持推荐质量的前提下，稳定补足候选池，并让缓存候选与实时候选共享同一套排序依据。

**Architecture:** 扩展 `content_cache` 持久化相关性与候选层级，在 discovery engine 内加入“主发现 + 分层补货”流程，并统一 recommendation 对实时结果和缓存结果的排序口径。运行时触发逻辑保持不变，只让 discovery 返回的候选更稳定、更可复用。

**Tech Stack:** Python 3.11+, sqlite3, dataclasses, existing discovery/recommendation stack, pytest, Ruff, MyPy

---

### Task 1: 为缓存候选补持久化评分字段

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_discovery_engine.py`

**Step 1: Write the failing test**

在 `tests/test_storage.py` 增加：

```python
def test_cache_content_persists_relevance_and_candidate_tier() -> None:
    ...
    db.cache_content(
        "BV1A",
        title="A",
        source="search",
        relevance_score=0.88,
        relevance_reason="fits profile",
        candidate_tier="primary",
    )
    row = db.get_cached_content(limit=1)[0]
    assert row["relevance_score"] == 0.88
    assert row["relevance_reason"] == "fits profile"
    assert row["candidate_tier"] == "primary"
```

再在 `tests/test_discovery_engine.py` 增加：

```python
async def test_discovery_engine_cache_results_preserves_relevance_fields() -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/python -m pytest tests/test_storage.py tests/test_discovery_engine.py -q
```

Expected: FAIL because schema / cache write logic does not store these fields.

**Step 3: Write minimal implementation**

- 在 `content_cache` schema 中新增：
  - `relevance_score REAL DEFAULT 0.0`
  - `relevance_reason TEXT DEFAULT ''`
  - `candidate_tier TEXT DEFAULT 'primary'`
- 补 migration helper，保证旧库自动加列
- 在 `_cache_results()` / `cache_content()` 时传递这三个字段

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python -m pytest tests/test_storage.py tests/test_discovery_engine.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/storage/database.py src/openbiliclaw/discovery/engine.py tests/test_storage.py tests/test_discovery_engine.py
git commit -m "feat: persist cached discovery relevance fields"
```

### Task 2: 为 discovery engine 增加补货流程

**Files:**
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`
- Modify: `tests/test_discovery_engine.py`
- Modify: `tests/test_refresh_runtime.py`

**Step 1: Write the failing test**

在 `tests/test_discovery_engine.py` 增加：

```python
async def test_discovery_engine_backfills_when_primary_results_too_few() -> None:
    ...
```

覆盖：
- 第一轮返回不足 12 条
- engine 会触发补货
- 返回结果包含 `candidate_tier="backfill"` 的候选

再补：

```python
async def test_discovery_engine_skips_backfill_when_primary_results_enough() -> None:
    ...
```

在 `tests/test_refresh_runtime.py` 增加：

```python
async def test_refresh_controller_uses_backfilled_candidates_for_recommendations() -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/python -m pytest tests/test_discovery_engine.py tests/test_refresh_runtime.py -q
```

Expected: FAIL because discovery currently has no backfill stage.

**Step 3: Write minimal implementation**

在 `ContentDiscoveryEngine` 中新增：
- 主候选目标数，例如 `target_primary_count=12`
- `backfill_target_count=18`
- `_run_primary_discovery(...)`
- `_run_backfill(...)`
- `_merge_and_rank(...)`

补货顺序：
- 优先扩 `search`
- 然后放宽其它策略
- 最后从缓存读取高分未推荐内容

如果不想扩公共接口，可在 engine 内部用补货参数创建策略实例。

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python -m pytest tests/test_discovery_engine.py tests/test_refresh_runtime.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/strategies/strategies.py tests/test_discovery_engine.py tests/test_refresh_runtime.py
git commit -m "feat: add discovery backfill pipeline"
```

### Task 3: 统一 recommendation 对缓存和实时候选的排序

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `tests/test_recommendation_engine.py`
- Modify: `tests/test_storage.py`

**Step 1: Write the failing test**

在 `tests/test_recommendation_engine.py` 增加：

```python
async def test_generate_recommendations_prefers_primary_then_relevance_then_recency() -> None:
    ...
```

再补缓存回读测试：

```python
async def test_generate_recommendations_reads_cached_relevance_score() -> None:
    ...
```

要求：
- 不再只按 `view_count` 取缓存项
- `primary` 优先于 `backfill`
- 分数高的缓存内容优先

**Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/python -m pytest tests/test_recommendation_engine.py tests/test_storage.py -q
```

Expected: FAIL because cache read currently drops `relevance_score` and `candidate_tier`.

**Step 3: Write minimal implementation**

- `get_unrecommended_content()` SQL 改成返回并排序：
  - `candidate_tier`
  - `relevance_score`
  - `last_scored_at`
  - `view_count`
- `_load_unrecommended_content()` 恢复这些字段到 `DiscoveredContent`
- `generate_recommendations()` 统一排序口径

**Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python -m pytest tests/test_recommendation_engine.py tests/test_storage.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/recommendation/engine.py src/openbiliclaw/storage/database.py tests/test_recommendation_engine.py tests/test_storage.py
git commit -m "feat: unify cached candidate recommendation ranking"
```

### Task 4: 更新文档并做回归验证

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

在文档中说明：
- discovery 现在有主发现和 backfill 两阶段
- `content_cache` 现在持久化相关性与候选层级
- recommendation 对缓存和实时候选采用统一排序

**Step 2: Run verification**

Run:
```bash
.venv/bin/python -m pytest tests/test_discovery_engine.py tests/test_recommendation_engine.py tests/test_storage.py tests/test_refresh_runtime.py -q
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m mypy src/
```

Expected: all pass.

**Step 3: Commit**

```bash
git add docs/modules/discovery.md docs/modules/recommendation.md docs/changelog.md
git commit -m "docs: document candidate supply upgrade"
```
