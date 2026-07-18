# Discovery Concurrency Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add conservative bounded concurrency to discovery so first-run `init` and `discover` stop doing obviously serial Bilibili and LLM work.

**Architecture:** Introduce one shared discovery concurrency controller with separate semaphores for Bilibili requests and LLM evaluations. Wire that controller through the discovery engine and strategies, then convert the slowest internal loops to bounded `asyncio.gather` flows while preserving current ranking and filtering semantics.

**Tech Stack:** Python 3.11, asyncio, Typer CLI, pytest

---

### Task 1: Add failing concurrency tests

**Files:**
- Modify: `tests/test_discovery_engine.py`
- Modify: `tests/test_search_strategy.py`
- Modify: `tests/test_trending_strategy.py`

**Step 1: Write the failing tests**

Add tests that cover:

- shared discovery concurrency controller caps LLM evaluation concurrency
- `SearchStrategy.discover()` issues multiple search requests concurrently but still respects `limit`
- `TrendingStrategy.discover()` can score multiple candidates concurrently without exceeding the cap

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_discovery_engine.py tests/test_search_strategy.py tests/test_trending_strategy.py -k "concurrency" -v`

Expected: FAIL because no shared controller or bounded concurrency exists yet.

**Step 3: Write minimal implementation**

Add only the smallest API surface needed by the tests.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_discovery_engine.py tests/test_search_strategy.py tests/test_trending_strategy.py -k "concurrency" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_discovery_engine.py tests/test_search_strategy.py tests/test_trending_strategy.py src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/strategies/strategies.py
git commit -m "test: add discovery concurrency coverage"
```

### Task 2: Add shared discovery concurrency controller

**Files:**
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Write the failing test**

Use the tests from Task 1 to define the controller behavior.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_discovery_engine.py::test_discovery_engine_limits_llm_evaluation_concurrency -v`

Expected: FAIL

**Step 3: Write minimal implementation**

In `src/openbiliclaw/discovery/engine.py`:

- add a small concurrency controller dataclass
- add bounded helpers for Bilibili and LLM tasks
- make `ContentDiscoveryEngine.evaluate_content()` use the controller

In `src/openbiliclaw/cli.py`:

- create one controller in `_build_discovery_engine()`
- pass it to engine and strategies

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_discovery_engine.py::test_discovery_engine_limits_llm_evaluation_concurrency -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/discovery/engine.py src/openbiliclaw/cli.py tests/test_discovery_engine.py
git commit -m "feat: add shared discovery concurrency controller"
```

### Task 3: Parallelize search and scoring paths conservatively

**Files:**
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`
- Test: `tests/test_search_strategy.py`
- Test: `tests/test_trending_strategy.py`
- Test: `tests/test_explore_strategy.py`

**Step 1: Write the failing test**

Add tests showing:

- search query/page fetches now overlap
- trending/explore candidate evaluation now overlaps
- results still keep expected BVIDs and thresholds

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_search_strategy.py tests/test_trending_strategy.py tests/test_explore_strategy.py -k "concurrency" -v`

Expected: FAIL

**Step 3: Write minimal implementation**

In `src/openbiliclaw/discovery/strategies/strategies.py`:

- add optional shared controller field to strategy dataclasses
- convert search request loops to bounded gather
- convert trending/explore evaluation loops to bounded gather
- keep result ordering deterministic before truncation

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_search_strategy.py tests/test_trending_strategy.py tests/test_explore_strategy.py -k "concurrency" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/discovery/strategies/strategies.py tests/test_search_strategy.py tests/test_trending_strategy.py tests/test_explore_strategy.py
git commit -m "feat: parallelize discovery search and scoring"
```

### Task 4: Parallelize related-chain scoring without changing BFS semantics

**Files:**
- Modify: `src/openbiliclaw/discovery/strategies/strategies.py`
- Test: `tests/test_related_chain_strategy.py`

**Step 1: Write the failing test**

Add a test that proves related-chain candidate scoring is now bounded-concurrent within a batch while preserving result selection.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_related_chain_strategy.py -k "concurrency" -v`

Expected: FAIL

**Step 3: Write minimal implementation**

- keep frontier traversal order intact
- only parallelize per-batch evaluation work
- do not widen Bilibili request fan-out beyond the shared controller

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_related_chain_strategy.py -k "concurrency" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/discovery/strategies/strategies.py tests/test_related_chain_strategy.py
git commit -m "feat: add bounded concurrency to related-chain scoring"
```

### Task 5: Update docs and verify integrated behavior

**Files:**
- Modify: `README.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`

**Step 1: Write the failing doc check**

Identify missing docs for:

- first-run discover now uses conservative concurrency
- init/discover are faster but still not guaranteed to be short

**Step 2: Run check to verify current state fails**

Run: `rg -n "并发|discover|init" README.md docs/modules/cli.md docs/modules/config.md docs/changelog.md`

Expected: missing explicit mention of conservative discovery concurrency

**Step 3: Write minimal documentation**

Document:

- discovery now uses conservative bounded concurrency
- first-run still may take minutes depending on provider/network
- no new user-facing config knobs were added

**Step 4: Run verification**

Run:

```bash
pytest tests/test_discovery_engine.py tests/test_search_strategy.py tests/test_trending_strategy.py tests/test_related_chain_strategy.py tests/test_explore_strategy.py tests/test_cli.py tests/test_config.py tests/test_docker_runtime.py -v
ruff check src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/strategies/strategies.py src/openbiliclaw/cli.py tests/test_discovery_engine.py tests/test_search_strategy.py tests/test_trending_strategy.py tests/test_related_chain_strategy.py tests/test_explore_strategy.py
git diff --check
```

Expected: all pass

**Step 5: Commit**

```bash
git add README.md docs/modules/cli.md docs/modules/config.md docs/changelog.md src/openbiliclaw/discovery/engine.py src/openbiliclaw/discovery/strategies/strategies.py src/openbiliclaw/cli.py tests/test_discovery_engine.py tests/test_search_strategy.py tests/test_trending_strategy.py tests/test_related_chain_strategy.py tests/test_explore_strategy.py
git commit -m "feat: optimize discovery with bounded concurrency"
```
