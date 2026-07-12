# Task 9 end-to-end verification report

Status: **BLOCKED — the strict live-admission gate rejected all eight real candidates**

## Deterministic evidence

- All scenarios use temporary SQLite and temporary memory only.
- User A starts with 16 ready rows and 602 raw rows under a ceiling of 600,
  consumes the pool to zero and observes real-service recovery in under one
  second for each of 50 sustained rounds, then runs post-refill maintenance.
- User A directly verifies no non-null discovery claim tokens and no active or
  waiting total/background permits remain after shutdown.
- A separate sixty-row copy scenario observes two simultaneous 30-item provider
  requests and measured expression fan-out exactly equal to two.
- An isolated User B database starts with ten ready rows plus twelve
  `zhihu-*` overflow rows and verifies canonical `zhihu` accounting and
  maintenance non-erasure.

## Live evidence

- Command: `OPENBILICLAW_REFILL_E2E=1 .venv/bin/pytest tests/test_refill_real_provider_integration.py -q -s`
- Result: exit 124 after 600.027 seconds (command timeout).
- No pytest summary or sanitized counters were produced before timeout, so the
  pending phase cannot be narrowed further without adding phase-only progress
  instrumentation or changing provider timeout policy.
- This is a failed verification, not a skip or pass.
- No API keys, Cookies, prompts, profile fields, titles, descriptions, content
  bodies, or model responses were printed or retained in this report.

Second run after adding phase-only markers and explicit timeouts:

- Public Bilibili ranking succeeded with `fetched=8`.
- The configured normal registry entered candidate evaluation.
- The configured provider did not return response headers within the explicit
  180-second evaluation timeout; pytest failed in the provider HTTP receive
  path (`1 failed in 183.77s`).
- Evaluation/admission/copy/maintenance/interactive counters were therefore not
  fabricated and the live acceptance gate remains failed.

### SenseTime-compatible provider rerun

- Exact command used the explicit opt-in controls:
  `OPENBILICLAW_REFILL_E2E=1`,
  `OPENBILICLAW_REFILL_CONFIG=/Users/white/workspace/OpenBiliClaw/config.toml`,
  and `OPENBILICLAW_REFILL_PROVIDER=openai_compatible`. The test loads that
  path only when explicitly supplied and clones the provider default in memory;
  it never writes config or logs credentials.
- A 30-second normal-registry completion probe succeeded in **5.6 seconds**.
- The full temporary-DB/read-only-ranking run reached `fetched=8`, then failed
  its intentional admission assertion in **33.57 seconds**: `evaluated=8`,
  `passing_scores=0`, `admitted=0`, `rejected=8`. It correctly did not proceed
  to copy, maintenance, or interactive-reservation phases.
- A follow-up aggregate-only diagnostic confirmed one JSON-mode `system/user`
  evaluator request via `openai_compatible` / `deepseek-v4-flash`: the response
  parsed as a valid object containing eight scored entries, all eight identifiers
  matched, all eight candidates resolved (zero parser-unresolved), every score
  fell below `0.60`, and all eight durable statuses were
  `rejected_low_score`.
- This is an admission/data failure under the unchanged strict acceptance rule,
  not a provider routing, response-shape, parser, authentication, or timeout
  failure. No threshold, profile, source data, or provider configuration was
  changed to manufacture a pass; no raw response, prompt, content body, Cookie,
  or key was printed.

## Guard and focused checks

- Live integration remains opt-in and is skipped when its explicit flag is
  absent.
- Repository-wide Ruff and MyPy checks pass; current exact affected counts are
  recorded in the review-remediation section below.

## Remaining gates

The mandatory live provider verification remains failed because this real
candidate batch did not satisfy strict admission. All deterministic gates were
subsequently run:

- Full Python suite: `4255 passed, 36 skipped` in 337.67 seconds. The 2288
  warnings are existing FastAPI/websocket deprecation warnings.
- Ruff: `ruff check src/ tests/` passed.
- MyPy: 189 source files checked with no issues.
- Extension: 711 tests passed; TypeScript typecheck and production build passed.
- Concurrency/cancellation soak: 50/50 rounds passed; each round ran 96 gate,
  candidate-evaluator, and expression-coordinator tests.

Contract/document/boundary audit:

- No separately committed destructive trim composition remains in runtime.
- No plan placeholders were found.
- Legacy `llm.concurrency=3` text matches are explicit compatibility/design
  examples; `candidate_eval_concurrency=3` is intentionally unchanged.
- Runtime `precompute_pool_copy()` references are compatibility branches used
  only when `ExpressionCopyCoordinator` is absent, plus one `limit=0` delight
  fallback. Normal long-running production wiring notifies the coordinator.
- All eight module documents, four architecture surfaces, configuration sample,
  and changelog are changed on the branch and describe the new flow.
- `git diff main...HEAD --check` passed and the worktree was clean before this
  report update.
- Soul changes only inject/reuse the runtime gate and update the default total
  concurrency. No Soul prompt, token-budget, pricing, or cost behavior changed.

## Review remediation

- Replaced the deterministic direct-gate fake with a real `LLMService` backed
  by a keyed controlled registry. Every deterministic provider request now
  traverses production caller classification, the shared gate, registry
  dispatch, parsing, admission and copy persistence.
- Split User A and User B into independent temporary SQLite and memory
  scenarios. User A performs maintenance before and after refill, consumes the
  pool to zero and observes recovery in under one second for each of 50 rounds,
  and verifies claim plus active/waiting permit cleanup. User B independently
  verifies ten ready Zhihu rows plus twelve overflow rows.
- Kept the production expression coordinator's three-second tail unchanged;
  sustained rounds create threshold-sized work rather than shortening it.
- Provider-boundary monitors now measure total/background gate peaks,
  expression batch sizes and expression fan-out. Live monitoring additionally
  counts registry calls and classified transient registry failures.
- Removed literal live summary metrics. The live summary prints only measured
  values and omits metrics unavailable at the registry boundary.
- All three live `LLMService` instances receive the exact same
  `module_overrides_from_config()` mapping.
- Added structural regressions for literal metrics, override propagation, real
  service routing, and isolated scenario definitions.

Second review remediation verification:

- Added a deterministic production-component companion with sixty durable
  pending-copy rows. A provider-ingress barrier observed two expression calls
  simultaneously active, each containing exactly thirty rows; measured copy
  fan-out was exactly two and the shared gate remained within total/background
  bounds.
- User A cleanup now directly executes SQL against `discovery_candidates` and
  asserts zero rows with a non-null `claim_token`, in addition to lifecycle
  status and active/waiting permit checks.
- Live metrics record `provider_round_count` for every actual registry
  invocation. A classified transient stores only an in-memory SHA-256
  fingerprint over caller/module, exact canonical messages and structured
  request parameters. Only a later invocation with the same fingerprint
  increments `transient_retry_count`; unrelated requests from the same caller
  do not count. Raw content and hashes are never printed.
- A controlled observer regression proves one transient plus one subsequent
  invocation yields two provider rounds, one transient failure, and one retry;
  the first failure alone yields no retry. A second regression proves a
  candidate-A transient followed by independent candidate-B success remains at
  zero retries.
- Fresh deterministic/contract/live-guard suite: `10 passed, 1 skipped in
  16.64s`; the skipped test is the explicit live provider test and was not
  rerun against the stalled local provider.
- Expanded affected suite (LLM service/gate, evaluator coordinator, expression
  coordinator and recommendation engine): `239 passed in 11.36s`.
- Repository-wide Ruff and MyPy (189 source files), plus `git diff --check`,
  passed after the final fingerprint change.
