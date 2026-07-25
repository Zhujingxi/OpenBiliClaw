# Final evaluator cap fix report

Status: **PASS**

## Root cause

The inherited continuous-evaluation implementation allowed eight candidate
workers at three independent boundaries:

- discovery config and `PUT /api/config` used `max_value=8`;
- `effective_candidate_eval_workers()` clamped desired workers to eight;
- `CandidateEvalCoordinator` accepted eight workers directly.

The popup and desktop settings inputs also exposed `max=8`. A configuration
file with `candidate_eval_concurrency=8` therefore loaded as eight, and
`effective_candidate_eval_workers(8, 9)` returned eight, exceeding the
approved 3 × 30 = 90 raw in-flight bound.

## Fix

- Bound config loading and API updates to `1..3`, preserving the existing
  out-of-range-to-default (`3`) normalization policy.
- Bound the API response model to `1..3`.
- Bound both the effective worker derivation and direct coordinator
  construction to three, while retaining the existing 30-item batch cap.
- Set popup and desktop numeric inputs to `max=3`.
- Updated the example config, module docs, and changelog to state the
  3-worker / 30-item / 90-raw contract.

No global total/background LLM capacity derivation, provider routing, profile,
admission threshold, or cost behavior changed.

## TDD evidence

Before implementation, new regressions failed for the intended reasons:

- TOML `candidate_eval_concurrency=8` loaded as `8`;
- `effective_candidate_eval_workers(8, 9)` returned `8`;
- the direct coordinator accepted `worker_count=8`;
- the API response model accepted `4`;
- `PUT /api/config` persisted `8`;
- the desktop setting exposed `max=8`.

After implementation, the same tests pass. The config regression preserves
values 1, 2, and 3, and proves a persisted 8 normalizes to 3. The coordinator
regression proves exactly three 30-item claims (90 raw) are in flight.

## Verification

- Targeted config/coordinator/API/desktop/global-gate suite: `656 passed`
- Extension tests: `711 passed`
- Extension typecheck and production build: passed
- `ruff check src/ tests/`: passed
- `mypy src/`: `Success: no issues found in 189 source files`
- Full Python suite: `4302 passed, 17 skipped` in `299.29s`
- `git diff --check`: passed
