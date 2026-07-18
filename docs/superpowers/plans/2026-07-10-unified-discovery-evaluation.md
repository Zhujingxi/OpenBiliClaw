# Unified Discovery Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every source/strategy scoring shortcut except the explicit `explore` exception and enforce that policy from evaluation through final serving.

**Architecture:** Put the source-aware threshold rule in one dependency-light admission module. Reuse it in the candidate pipeline, cache-write convergence and database serving gates, then wire the OpenClaw compatibility runtime to the same candidate pipeline used by the API runtime.

**Tech Stack:** Python, pytest, Ruff, MyPy, Markdown.

## Global Constraints

- `explore` is the only discovery context allowed relaxed topical distance.
- No platform or non-explore strategy may receive a score floor, bonus, lower standard, or post-hoc relevance rationale.
- Preserve prompt-cache invariance: dynamic source/profile data remains in the user message.
- The global admission floor remains configurable; the sole relaxed final-serving floor is exact `explore` at `0.58`.
- Missing relevance scores fail closed as `0.0`.

---

### Task 1: Lock the unified evaluator contract

**Files:**
- Modify: `tests/test_llm_prompts.py`
- Modify: `src/openbiliclaw/llm/prompts.py`

**Interfaces:**
- Consumes: `build_content_evaluation_prompt(...)` and `build_batch_content_evaluation_prompt(...)`.
- Produces: identical non-explore scoring semantics in both builders.

- [x] Add a regression test that requires the unified rule and rejects the old source-specific shortcuts.
- [x] Run the test and verify it fails because the old prompt still contains the trending floor.
- [x] Replace the source-specific scoring clauses in both static system prompts with the unified rule and sole `explore` exception.
- [x] Run the regression and complete prompt test module.

### Task 2: Document and verify

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/changelog.md`

**Interfaces:**
- Consumes: the evaluator contract from Task 1.
- Produces: user-facing documentation of the unified scoring semantics.

- [x] Update the discovery evaluation/admission documentation.
- [x] Add a changelog bullet under the current release block.
- [x] Run Ruff, MyPy, the targeted discovery tests, and attempt the full pytest suite; record unrelated dirty-tree blockers separately.
- [x] Inspect the final diff and preserve unrelated working-tree changes.

### Task 3: Centralize and enforce admission policy

**Files:**
- Create: `src/openbiliclaw/discovery/admission.py`
- Modify: `src/openbiliclaw/discovery/candidate_pipeline.py`
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `tests/test_discovery_candidate_pipeline.py`
- Modify: `tests/test_discovery_engine.py`
- Modify: `tests/test_storage.py`

**Interfaces:**
- Produces: `effective_admission_threshold(source_strategy, admission_min_score, requested_threshold=None) -> float`.
- Consumes: exact source strategy, configured global floor and optional candidate threshold.

- [x] Add failing tests proving non-explore cannot lower the global floor, exact explore uses at least `0.58`, low-score cache writes are skipped, and a missing score is stored as `0.0`.
- [x] Run the new tests and verify the expected failures.
- [x] Implement the pure admission policy and replace the pipeline-local threshold logic.
- [x] Add the cache convergence guard and fail-closed storage default.
- [x] Run the focused tests until green.

### Task 4: Make final serving honor the sole explore exception

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_delight_scorer.py`

**Interfaces:**
- Consumes: `effective_admission_threshold(...)` and `EXPLORE_ADMISSION_MIN_SCORE`.
- Produces: one reusable SQL predicate/parameter helper for all user-visible pool exits.

- [x] Add failing database tests proving `explore=0.58` is servable while non-explore `0.58` is excluded from normal pool, cached backfill, platform floor and delight retrieval.
- [x] Run the tests and verify failures at the old global-only SQL gate.
- [x] Apply the source-aware predicate to serving, cached backfill, availability counts, copy backfill and delight retrieval; preserve exact-source matching.
- [x] Run storage, recommendation and delight tests until green.

### Task 5: Wire OpenClaw compatibility runtime to the unified pipeline

**Files:**
- Modify: `src/openbiliclaw/integrations/openclaw/bootstrap.py`
- Modify: `tests/test_openclaw_adapter.py`

**Interfaces:**
- Produces: one `DiscoveryCandidatePipeline` shared by the controller, Douyin producer and YouTube producer.

- [x] Add a failing bootstrap test that captures producer/controller kwargs and requires object identity for the shared pipeline.
- [x] Run the test and verify the pipeline is currently absent.
- [x] Construct the pipeline from configured `admission_min_score`, update the database policy, and inject it into all three consumers.
- [x] Run OpenClaw adapter and proactive E2E tests until green.

### Task 6: Documentation and final verification

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/changelog.md`

- [x] Document cache fail-closed semantics, the exact `explore=0.58` exception and OpenClaw pipeline convergence.
- [x] Add a changelog bullet under the current release.
- [x] Run targeted tests, full pytest, Ruff and MyPy; record unrelated dirty-tree blockers separately.
- [x] Inspect the final diff and preserve unrelated working-tree changes.
