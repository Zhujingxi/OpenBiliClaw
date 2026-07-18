# Eval Reason Diet — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-18-eval-reason-diet-spec.md`](./2026-07-18-eval-reason-diet-spec.md)
> **Status:** r1 — user-approved design (options 1+2); single implementation task; replay gate
> is run by the supervisor with the real provider before landing.
> **Execution order:** Task 1, then supervisor-run gate.
> **Tech:** worktree venv — `.venv/bin/python -m pytest tests/<file> -q`,
> `.venv/bin/ruff format src/ tests/ && .venv/bin/ruff check src/ tests/`, `.venv/bin/mypy src/`;
> always `git -C <absolute worktree path>`.

**Invariants that MUST hold — re-read before each task:**

- System prompts stay static module constants; the 0.5 floor is baked text, never per-call.
- 0.5 < every admission path (0.60 default, 0.58 explore) — reason-less items can never be
  admitted or reach the delight fallback.
- Empty-reason parsing already maps to `""` everywhere; no assertion may be weakened to make
  this pass.
- Admitted-item reasons: one conversational sentence, ≤30 个字 (delight fallback may show them
  verbatim).
- The replay gate (A/A envelope, then A/B) decides the merge — implementation lands on the
  branch but is not release-ready until the gate numbers are recorded.

### Task 1: Reason contract in both evaluation builders

**Files:** modify `src/openbiliclaw/llm/prompts.py` (batch + single evaluation system prompts);
tests `tests/test_llm_prompts.py` (prompt-contract assertions), spot-verify empty-reason flow in
`tests/test_discovery_engine.py` fixtures if any assert non-empty reasons; docs
`docs/modules/llm.md`, `docs/modules/discovery.md`, `docs/changelog.md`.

**Interfaces:** Consumes: existing static system-prompt constants. Produces: identical JSON
output schema; only the reason-writing instruction changes (skip <0.5, ≤30字 otherwise).

**Steps:**

- [x] Write failing prompt-contract tests: batch + single system prompts contain the 0.5-skip
      instruction and the ≤30字 cap phrasing.
- [x] Run `.venv/bin/python -m pytest tests/test_llm_prompts.py -q`; confirm the new tests FAIL.
- [x] Edit both system-prompt constants; keep them 100% static.
- [x] Rerun focused tests → PASS; run `test_prompt_builder_system_messages_are_call_invariant`.
- [x] Trace the empty-reason path once (eval parse → cache entry → candidate persistence →
      delight fallback `.strip()` skip) and add one regression test if any link lacks coverage.
- [x] Full suite; `ruff format` + `ruff check` + `mypy src/` clean.
- [x] Single commit (conventional; `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`),
      tick this plan's boxes, changelog bullet under the current version block.

**Acceptance:**

- Numeric gate: prompt-contract tests green; full suite has 0 new failures vs baseline
  (5109 passed / 47 skipped; two known flakies — `test_pool_maintenance.py::
  test_maintenance_defers_quickly_when_interactive_writer_owns_lock` and
  `test_saved_sync_service.py::test_detached_timeout_restarts_heartbeat_after_later_failure` —
  pass-standalone rule applies).

## Supervisor gate (not an agent task)

- A/A envelope then A/B (old vs new prompt) via `scripts/run_profile_diet_ab.py` with
  `--db/--config` pointing read-only at the production DB, real provider (sensenova
  deepseek-v4-flash per the testing convention). Gate: admission flips and signed drift within
  the A/A envelope on ≥100 candidates. Numbers recorded in the landing summary.

## Verification after merge

- 48h `openbiliclaw cost --by caller`: output tokens/call drop on `discovery.evaluate_batch` /
  `recommendation.evaluate_batch`; delight cards spot-checked for presentable fallback reasons.
- Rollback trigger: gate failure → revert the single commit; post-merge quality complaint →
  same.

## Explicitly out of scope

- Expression-copy length caps; delight fallback chain changes (option 3 was rejected);
  admission thresholds; reason storage schema.
