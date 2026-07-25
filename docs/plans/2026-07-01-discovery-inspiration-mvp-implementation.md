# Discovery Inspiration MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a first experimental implementation of Exa-inspired discovery query expansion with traceable inspiration, lateral expansion, and profile-curator metadata.

**Architecture:** Start with durable metadata and pure parsing/selection helpers before wiring the planner. The MVP stores inspiration seeds, lateral expansions, and angle provenance. It stays disabled by default, but the experimental replacement flag can now bypass the merged keyword planner for regular search keywords and can also fill Bilibili's `keyword_kind="explore"` pool when explore is due.

**Follow-up spec:** The MVP's fixed seed-query step is intentionally limited.
The next iteration is specified in
[`2026-07-02-like-secondary-interest-query-generation-spec.md`](./2026-07-02-like-secondary-interest-query-generation-spec.md):
sample positive like-derived secondary interests by coverage, let an LLM
brainstorm search probes, ground those probes with Exa, then generate
platform-specific keywords under system-side interest/lens/content-type quotas.

**Tech Stack:** Python 3.12, SQLite via `openbiliclaw.storage.database.Database`, pytest, Ruff, MyPy-compatible typed helpers.

---

### Task 1: Persistence For Inspiration And Expansion

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_discovery_inspiration.py`

**Steps:**
1. Write failing tests that a new database creates inspiration and expansion cache tables.
2. Write failing tests for upserting inspiration seeds and lateral expansions.
3. Add schema creation in `Database._ensure_discovery_keywords_table()`.
4. Add minimal DAO methods for upsert/list/update-yield behavior.
5. Run `uv run --extra dev pytest tests/test_discovery_inspiration.py`.

### Task 2: Pure Inspiration Pipeline Helpers

**Files:**
- Create: `src/openbiliclaw/discovery/inspiration.py`
- Test: `tests/test_discovery_inspiration.py`

**Steps:**
1. Write failing tests for normalizing Exa preview items into inspiration seeds.
2. Write failing tests for bounded lateral expansion filtering and curator decisions.
3. Implement dataclasses and pure helper functions with no network calls.
4. Run the targeted tests.

### Task 3: Keyword Metadata Plumbing

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_discovery_keywords.py`

**Steps:**
1. Write failing tests that keyword rows can persist optional `aspect_id`, `inspiration_id`, `expansion_id`, `angle_id`, and query kind metadata.
2. Extend `discovery_keywords` schema and `insert_keywords()` with backwards-compatible optional metadata.
3. Keep existing callers unchanged.
4. Run targeted keyword tests.

### Task 4: Documentation And Verification

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/changelog.md`

**Steps:**
1. Document the MVP as disabled-by-default plumbing for Exa-inspired query expansion.
2. Run `uv run --extra dev pytest tests/test_discovery_inspiration.py tests/test_discovery_keywords.py tests/test_keyword_planner.py`.
3. Run `uv run --extra dev ruff check src/openbiliclaw/storage/database.py src/openbiliclaw/discovery/inspiration.py tests/test_discovery_inspiration.py`.

---

## Full Spec Integration Tasks

### Task 5: Add Inspiration Planner Configuration

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Test: `tests/test_config.py`

**Steps:**
1. Write failing config tests for default-off `inspiration_search_enabled` and bounded inspiration knobs.
2. Add fields to `DiscoveryConfig`.
3. Load the fields from `[discovery]`.
4. Render the fields in generated config output.
5. Run targeted config tests.

### Task 6: Add Exa Provider Abstraction

**Files:**
- Create: `src/openbiliclaw/discovery/inspiration_provider.py`
- Test: `tests/test_discovery_inspiration_provider.py`

**Steps:**
1. Write failing tests for parsing mcporter Exa output into `ExaPreviewItem`.
2. Add `InspirationSearchProvider` protocol and `McporterExaInspirationProvider`.
3. Keep network / process failures contained to the provider caller.
4. Run targeted provider tests.

### Task 7: Add Aspect Window And Query Realization Helpers

**Files:**
- Modify: `src/openbiliclaw/discovery/inspiration.py`
- Test: `tests/test_discovery_inspiration.py`

**Steps:**
1. Write failing tests for large profile aspect-window selection.
2. Write failing tests for generating concrete keyword candidates and metadata from curated expansions.
3. Implement the pure helper layer without LLM or network calls.
4. Run targeted inspiration tests.

### Task 8: Integrate Inspiration Stage Into KeywordPlanner

**Files:**
- Modify: `src/openbiliclaw/runtime/keyword_planner.py`
- Test: `tests/test_keyword_planner.py`

**Steps:**
1. Write failing tests that disabled inspiration leaves planner behavior unchanged.
2. Write failing tests that enabled inspiration uses provider search previews, curator/detail LLM JSON, stores seeds / expansions, and inserts metadata-bearing keywords.
3. Add optional `inspiration_provider` injection.
4. Add a bounded `_run_inspiration_stage()` after merged keyword generation.
5. Run targeted keyword planner tests.

### Task 9: Backfill Inspiration Yield From Keyword Yield

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_discovery_inspiration.py`

**Steps:**
1. Write failing idempotency tests: the first `increment_keyword_yield()` bumps keyword + inspiration + expansion yield, duplicate content does not double-count.
2. Read keyword metadata after the keyword-yield ledger insert succeeds.
3. Increment matching inspiration / expansion counters best-effort.
4. Run targeted inspiration and keyword-yield tests.

### Task 10: Documentation And Verification

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/storage.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`

**Steps:**
1. Document the disabled-by-default full pipeline and configuration knobs.
2. Run `uv run --extra dev ruff format src/ tests/`.
3. Run `uv run --extra dev ruff check src/ tests/`.
4. Run `uv run --extra dev mypy src/`.
5. Run `uv run --extra dev pytest`.
