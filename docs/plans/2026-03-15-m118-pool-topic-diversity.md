# 候选池 Topic 多样性 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add stable topic grouping to discovery-pool candidates so each recommendation batch covers more than one direction instead of repeatedly surfacing near-identical topics.

**Architecture:** Extend `content_cache` with a lightweight `topic_key`, populate it from discovery strategies, and update recommendation selection to bucket by topic before score-based backfill. Keep the approach heuristic and deterministic; no new taxonomy tables or embedding clustering.

**Tech Stack:** SQLite, existing discovery strategies, recommendation engine, pytest, mypy, Ruff.

---

### Task 1: Persist `topic_key` in the discovery pool

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**

Add tests that verify:
- `cache_content(..., topic_key="国际时事:地缘政治")` persists `topic_key`
- pool candidate queries return the stored `topic_key`

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_storage.py -q`
Expected: FAIL because `topic_key` is not stored or returned yet.

**Step 3: Write minimal implementation**

Update schema migration helpers and `cache_content()` / pool query methods to read and write `topic_key`.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_storage.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/storage/database.py tests/test_storage.py
git commit -m "feat: persist topic keys in discovery pool"
```

### Task 2: Make recommendation selection diversify by topic

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Test: `tests/test_recommendation_engine.py`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/changelog.md`

**Step 1: Write the failing test**

Add a reshuffle test where:
- two highest-scoring candidates share the same topic
- lower-scoring candidates exist in different topics
- expectation: the batch includes different topics before backfilling duplicates

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_recommendation_engine.py -q`
Expected: FAIL because the engine currently picks by score only.

**Step 3: Write minimal implementation**

Add a shared selection helper that:
- buckets candidates by `topic_key`
- picks one from each topic first
- backfills remaining slots by score
- falls back gracefully when `topic_key` is missing

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_recommendation_engine.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/recommendation/engine.py tests/test_recommendation_engine.py docs/modules/recommendation.md docs/changelog.md
git commit -m "feat: diversify recommendation batches by topic"
```

### Task 3: Populate `topic_key` in high-impact discovery strategies

**Files:**
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`
- Test: `tests/test_search_strategy.py` or `tests/test_discovery_engine.py`
- Test: `tests/test_related_chain_strategy.py`

**Step 1: Write the failing test**

Add tests that verify:
- `SearchStrategy` candidates carry a query-derived `topic_key`
- `RelatedChainStrategy` candidates carry a stable seed/topic-derived `topic_key`

**Step 2: Run test to verify it fails**

Run:
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_related_chain_strategy.py -q`
- plus whichever test file covers `SearchStrategy`

Expected: FAIL because candidates do not expose `topic_key` yet.

**Step 3: Write minimal implementation**

Add lightweight topic key builders:
- normalize query/domain/seed context
- attach `topic_key` to discovered content

Avoid new LLM calls.

**Step 4: Run test to verify it passes**

Run the same tests again and confirm PASS.

**Step 5: Commit**

```bash
git add src/openbiliclaw/discovery/strategies/strategies.py tests/test_related_chain_strategy.py tests/test_search_strategy.py
git commit -m "feat: tag discovery candidates with topic keys"
```

### Task 4: Add light topic compression before pool insertion

**Files:**
- Modify: `src/openbiliclaw/discovery/engine.py`
- Test: `tests/test_discovery_engine.py`

**Step 1: Write the failing test**

Add a test where one discovery run returns many high-scoring items with the same `topic_key`.
Expectation:
- top results kept for return/cache are not dominated by a single repeated topic when alternatives exist

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_discovery_engine.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**

Add a small pre-cache compression step in the discovery engine:
- same topic gets lightly capped when enough alternatives exist
- quality ranking still preserved within each topic

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_discovery_engine.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/discovery/engine.py tests/test_discovery_engine.py
git commit -m "feat: compress repeated topics before caching"
```

### Task 5: Full verification and docs sync

**Files:**
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/memory.md` if pool semantics change
- Modify: `docs/changelog.md`
- Modify: `docs/v0.1-todolist.md` if a milestone line should be checked

**Step 1: Update docs**

Document:
- `topic_key` in discovery pool
- batch diversification behavior
- recommendation fallback/backfill rules

**Step 2: Run verification**

Run:
- `PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/`
- `PYTHONPATH=src .venv/bin/python -m mypy src/`
- `PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected:
- PASS

**Step 3: Commit**

```bash
git add docs/modules/recommendation.md docs/modules/memory.md docs/changelog.md docs/v0.1-todolist.md
git commit -m "docs: record topic-aware pool diversification"
```
