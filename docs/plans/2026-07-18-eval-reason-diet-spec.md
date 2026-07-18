# Eval Reason Diet Spec — cut evaluation output tokens without losing the fallback face

**Created:** 2026-07-18
**Scope:** the `reason` field in batch/single content-evaluation prompts
(`llm/prompts.py` batch + single builders), parser tolerance in
`discovery/engine.py` / `recommendation/engine.py`, and the replay quality gate.
**Out of scope:** expression-copy length caps (separate decision), the delight
fallback chain itself (`recommendation/engine.py:1927` keeps its
`relevance_reason` exit), score semantics, admission thresholds.

## Goal

Every 30-item evaluation batch asks the model to write a free-text `reason` per
item. Output tokens cost 5–10× cached input, and most candidates score below
admission and are discarded — their reasons are pure waste. Target: cut batch
evaluation output tokens materially (expected 30–50% on typical batches where
most items score low) while keeping reasons for every candidate that could ever
reach a user surface.

User decision (2026-07-18): adopt options 1 + 2 — skip reasons below a floor,
cap and humanize the rest. Option 3 (removing the delight fallback exit) was
explicitly NOT adopted, so admitted-item reasons must stay presentable.

## Design invariants (MUST hold)

1. **Static system prompts** (CLAUDE.md cache convention): the skip floor is a
   fixed constant baked into the system prompt text, not a per-call value.
2. **Skip floor strictly below every admission path:** floor = **0.5**;
   `admission_min_score` default is 0.60 and the explore path uses exactly 0.58
   (`discovery/admission.py`), so a reason-less item can never be admitted,
   never enter the pool, and never reach the delight fallback chain.
3. **Parser tolerance is already total and must stay so:** empty/missing
   `reason` maps to `""` (`item_result.get("reason", "")`); no new failure mode
   for reason-less items. Eval cache entries with empty reason remain valid.
4. **Presentable admitted reasons:** items at/above 0.5 get a reason of **≤30
   个字, one conversational sentence** (it can surface verbatim via the delight
   fallback `pool_expression → relevance_reason → topic → generic`).
5. **Measure before you cut (quality gate):** writing reasons may act as
   implicit chain-of-thought that improves score quality, so this change can
   shift scores, not just output size. It MUST pass the golden-set replay gate
   (`scripts/run_profile_diet_ab.py`): same-day A/A noise envelope first, then
   A/B (old vs new prompt); admission-flip and signed-drift metrics within the
   A/A envelope on ≥100 real evaluated candidates. No gate, no merge.

## Current diagnosis

- Batch builder `build_batch_content_evaluation_prompt` and single builder
  `build_content_evaluation_prompt` (`llm/prompts.py`) request an unconditional
  free-text `reason` per item; no length guidance exists.
- `reason` consumers: stored as `relevance_reason` (candidates + DB row
  `recommendation/engine.py:3258`), surfaced ONLY via the delight fallback
  chain (`recommendation/engine.py:1923-1937`); it is not an input to
  expression copy (content_summary carries no reason field) and appears in no
  UI code directly.
- Score distribution makes sub-0.5 the volume majority on typical discovery
  batches (irrelevant candidates dominate raw discovery), so option 1 carries
  most of the savings.

## Phase design (single phase)

- System prompts (batch + single, kept static): instruct — `score < 0.5` →
  `"reason": ""`;otherwise one conversational sentence ≤30 个字.
- No parser/code changes expected beyond tests; verify the empty-reason path
  end-to-end (eval cache write, candidate persistence, delight fallback skips
  empty reason — it already `.strip()`s).
- Tests: prompt-text assertions updated; a shape test pinning that the
  instruction block contains the floor and the cap; existing
  `test_prompt_builder_system_messages_are_call_invariant` stays green.
- Gate (run by the supervisor, real provider): A/A envelope + A/B via
  `scripts/run_profile_diet_ab.py --db/--config` against the production DB
  (read-only). Record numbers in the landing commit/PR.

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Skip sub-0.5 reasons | majority of batch items stop emitting reason text; measure output tokens/call via `openbiliclaw cost --by caller` (discovery.evaluate_batch, recommendation.evaluate_batch) before/after |
| ≤30字 cap on the rest | bounds the remaining reason output; keeps delight fallback presentable |

## Documentation obligations

- `docs/modules/llm.md` prompt-contract note; `docs/modules/discovery.md`
  evaluation section (reason contract); `docs/changelog.md` bullet under the
  current version block; `docs/profile-usage.md` untouched (no profile-surface
  change).

## Gate results (2026-07-18, supervisor-run)

Method: relative gate (absolute thresholds are unusable on this gateway — the
same-day A/A control alone flips 21%). Sample: 100 real evaluated candidates
from the production DB (read-only), sensenova `deepseek-v4-flash`,
temperature 0, replay `max_tokens=16384` headroom on both arms.

| Metric | A/A envelope | A/B (reason-diet) | Verdict |
| --- | --- | --- | --- |
| Mean signed delta (B−A) | −0.0383 | +0.0303 | within envelope |
| Admission-rate delta | −15.0pp | +5.0pp | within envelope |
| Flip rate | 21% | 17% | below noise |
| Per-platform signed delta | ±0.007…0.074 | +0.004…0.054 | within envelope |

**PASS** — the reason contract change is statistically indistinguishable from
same-day gateway noise; residual drift is mildly positive (no admission
shrinkage). Harness: `--arm-b reason-diet` added to
`scripts/run_profile_diet_ab.py` (arm A surgically restores the legacy
instruction; staleness guard raises if the live prompt diverges from the
recorded snippets).
