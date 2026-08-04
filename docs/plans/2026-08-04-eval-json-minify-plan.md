# Evaluator JSON Minify Implementation Plan

**Spec:** `docs/plans/2026-08-04-eval-json-minify-spec.md`

## Task 1 — Freeze the boundary

- [x] Record the real reason-off result and identify prompt input as the dominant token component.
- [x] Measure current 100-row prompt character composition without provider calls.
- [x] Define JSON-minify as a whitespace-only experiment; defer field omission and batch changes.
- [x] Complete the field-consumer audit needed for the next, separate serialization-diet phase; it
      requires batch-local string IDs and forbids multi-member positional fallback before global IDs can
      leave the LLM wire safely.

## Task 2 — Opt-in compact renderer

**Owner:** `luna_max_cache`

- [x] Add an opt-in compact renderer while preserving the existing pretty default.
- [x] Prove byte determinism, sorted keys, Unicode preservation and string-value preservation.
- [x] Run focused formatter/lint/tests; do not wire production.

## Task 3 — Replay-only arm

**Owner:** `luna_max_replay`

- [x] Add `--arm-b json-minify` with A pretty and B compact.
- [x] Scope the treatment without mutating module globals across concurrent calls.
- [x] Verify A/B JSON semantics, system prompt, route and runtime settings.
- [x] Extend privacy-safe artifact usage/cache/repair evidence and fail-closed aggregation.
- [x] Add focused replay tests; production defaults remain byte-identical.

## Task 4 — Root integration verification

- [x] Review all changes against the spec and repository prompt-cache convention.
- [x] Run JSON renderer, replay, prompt, discovery engine and candidate-pipeline focused tests.
- [x] Run Ruff, MyPy and `git diff --check`.
- [x] Run deterministic candidate-pipeline E2E and verify warm-cache zero-provider-call behavior.
- [x] Commit a clean replay experiment before calling the provider.

## Task 5 — Real replay and independent audit

- [ ] Run the exact 100×3 command from the spec on a clean commit.
- [ ] Independently recompute score/admission/Spearman and paired usage deltas from raw artifact data.
- [ ] Validate route/embedding/recall/cache/repair gates and artifact privacy.
- [ ] Record artifact path, commit, SHA-256, runtime, retries and any provider limitations.

## Task 6 — Production decision

- [ ] If every gate passes, wire compact JSON only into production batch evaluation.
- [ ] Bump eval-cache version and update `CLAUDE.md`, discovery docs and changelog.
- [ ] Rerun focused, full backend and applicable end-to-end tests on the final clean commit.
- [ ] If any gate fails, keep production pretty JSON and record the rejected result without tuning gates.

## Deferred independent experiments

These remain separate so their effects can be attributed:

1. omit empty/redundant candidate fields and use batch-local short IDs;
2. harden exact JSON schema/member completeness;
3. raise text batch size from 30 to 45;
4. calibrate embedding prefilter shadow → enforce;
5. score first, classify only near/above admission;
6. semantic sentence retrieval for long text bodies.
