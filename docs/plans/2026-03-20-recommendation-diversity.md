# Recommendation Diversity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce single-topic and single-source dominance in generated recommendation batches.

**Architecture:** Keep one recommendation selection pipeline, but make `generate` use balanced cached candidates and make diversified selection relax constraints in stages instead of fully discarding them during final backfill.

**Tech Stack:** Python, SQLite, pytest

---

### Task 1: Lock the regression with tests

**Files:**
- Modify: `tests/test_recommendation_engine.py`
- Modify: `src/openbiliclaw/recommendation/engine.py`

**Step 1: Write the failing test**

- Add a `generate_recommendations()` regression test showing that one `related:*` topic should not occupy 5/10 slots.
- Add a cached-content regression test showing `generate_recommendations()` should preserve more source variety when cache is heavily skewed.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_recommendation_engine.py -k "generate_recommendations_limits_single_topic_dominance or generate_recommendations_balances_sources_from_cache" -v`

**Step 3: Write minimal implementation**

- Update candidate loading so `generate` uses a source-balanced cached batch.
- Replace unconditional final backfill with staged relaxation that preserves topic caps longer.

**Step 4: Run test to verify it passes**

Run the same targeted pytest command.

**Step 5: Commit**

```bash
git add tests/test_recommendation_engine.py src/openbiliclaw/recommendation/engine.py docs/plans/2026-03-20-recommendation-diversity-design.md docs/plans/2026-03-20-recommendation-diversity.md
git commit -m "fix: improve recommendation diversity"
```

### Task 2: Verify no regression in existing recommendation selection behavior

**Files:**
- Test: `tests/test_recommendation_engine.py`

**Step 1: Run focused recommendation tests**

Run: `pytest tests/test_recommendation_engine.py -v`

**Step 2: Check failures**

- If existing reshuffle diversity tests break, adjust implementation conservatively rather than weakening the new tests.

**Step 3: Final verification**

Run: `pytest tests/test_recommendation_engine.py -v`
