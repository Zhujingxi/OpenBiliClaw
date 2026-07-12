# Task 9 end-to-end verification report

Status: **PASS — deterministic and real SenseTime verification complete**

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
  path only when explicitly supplied and clones the selected default plus every
  LLMService module route in memory; it never writes config or logs credentials.
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
- Follow-up harness correction: an explicit live provider now also overrides
  `soul`, `discovery`, `recommendation`, and `evaluation` module providers and
  clears their old per-module model overrides, so an `evaluation=ollama`
  setting cannot silently win over the requested compatible provider. A guard
  test proves `discovery.evaluate_batch` resolves the explicit provider.

### Post-routing-review rerun

- After the focused routing review passed, the exact full command was rerun at
  harness head `467320d8` with the same explicit config/provider controls.
- Result: `1 failed in 49.17s`. Phases reached `ranking fetched=8` and then
  `evaluation_done evaluated=8 passing_scores=0 parser_unresolved=0
  admitted=0 rejected=8`.
- The strict admission assertion stopped the run before copy, maintenance, and
  interactive-reservation phases. This independently confirms that the routing
  correction did not mask a parser failure: all eight real candidates were
  evaluated but rated below the unchanged admission threshold. No threshold,
  profile, source data, or provider setting was changed after this result.

### Technology-ranking rerun

- The live fixture was narrowed to the public Bilibili technology ranking
  (`ranking_rid=188`, still anonymous and capped at eight) while keeping the
  synthetic software/technology profile, provider route, and `0.60` threshold
  unchanged.
- At harness head `9af1c0f1`, the full exact run reached
  `ranking_rid=188 fetched=8` and `evaluation_start`, then failed in
  **59.56 seconds** before any score/admission result was returned.
- The selected compatible provider rejected the JSON-object request with an
  HTTP 400 message-format policy error, then the exact route entered its
  rate-limit state. This is a provider request-compatibility failure, not a
  low-score admission result; copy, maintenance, and interactive phases did
  not run. No profile, threshold, source breadth, or provider configuration was
  changed after the failure.
- Aggregate contract reconstruction used the actual `LLMService` routing path
  with no provider request: all-ranking and technology-ranking calls both used
  `openai_compatible` / `deepseek-v4-flash`, chat-completions
  `response_format=json_object`, `complete_provider`, `system/user`, eight
  items, `discovery.evaluate_batch`, `max_tokens=16384`, `temperature=0.7`,
  disabled reasoning, and no module model override. Both pairs lacked a literal
  lowercase `json` token (the system prompt has uppercase `JSON`); only the
  technology user-message size was larger. The evidence supports a nonstandard
  case-sensitive provider JSON-object policy whose enforcement changed or was
  inconsistent after the prior all-ranking success; the later rate-limit is a
  downstream result, not the initial cause. No raw prompt, title, response, or
  credential was retained.

### Technology-ranking post-compatibility rerun

- After the reviewed structured JSON compatibility fix, the same exact
  `ranking_rid=188` / explicit-provider command passed at harness head
  `e3c7e325` in **83.83 seconds**.
- Sanitized live phases: `fetched=8`, `evaluated=8`, `passing_scores=4`,
  `parser_unresolved=0`, `admitted=4`, `rejected=4`, `copied=4`, and
  maintenance `available_before=4 available_after=4`.
- The live interactive reservation completed: total/background peaks were
  `4/3`, the fourth interactive slot entered, maximum copy batch was `4`, copy
  fan-out was `1`, provider rounds were `8`, and transient retry/failure counts
  were both `0`. The real provider, public anonymous Bilibili source, temporary
  SQLite/memory state, synthetic profile, and unchanged `0.60` threshold all
  remained in effect.

## Guard and focused checks

- Live integration remains opt-in and is skipped when its explicit flag is
  absent.
- Repository-wide Ruff and MyPy checks pass; current exact affected counts are
  recorded in the review-remediation section below.

## Final gates

All Task 9 verification gates now pass:

- Full Python suite: `4274 passed, 36 skipped` in 252.05 seconds. The 2288
  warnings are existing FastAPI/websocket deprecation warnings.
- Ruff: `ruff check src/ tests/` passed; the two touched LLM files are already
  formatted. A full `ruff format --check src/ tests/` still reports ten
  pre-existing, unrelated files that would be reformatted, so they were not
  modified by this branch.
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

## OpenClaw one-shot bounded-copy follow-up

- OpenClaw direct bootstrap now bounds one interactive replenishment wave at
  four source/evaluation/copy rows. Its copy callback calls
  `drain_pending_expression_copy(limit=4, max_extra_requests=0)`; the generic
  API/daemon default remains `limit<=60, max_extra_requests=6`.
- The deterministic bootstrap regression first failed against the old behavior:
  a four-item provider response with a valid two-item subset was split and
  retried. It now proves exactly one copy provider call, a two-row canonical
  subset, two durable pending-copy rows, and exactly one receipt/callback
  owner.
- Focused final guard command (with the real test intentionally opt-in and
  therefore skipped here) completed as `9 passed, 1 skipped in 3.47s`:
  OpenClaw bootstrap/controller, one-shot refresh caps, generic expression
  split-retry defaults, and the live-test skip guard.
- Formatting/lint command over touched production and test files passed;
  `mypy src/` reported no issues in 189 files; `git diff --check` passed.
- Real opt-in SenseTime/OpenAI-compatible run used a temporary database,
  anonymous public Bilibili technology ranking (`rid=188`), synthetic generic
  profile, default OpenClaw `pool_target=300` and `discovery_limit=30`. It
  passed in 45.82s wall time: evaluation `[4]` took 15.92s; two rows were
  admitted; one `recommendation.write_expression` request for batch two took
  10.89s; refresh was 28.42s and adapter end-to-end time 43.03s, below the
  unchanged 45s boundary. There was one admission callback, one controller
  copy callback, no cancellation, one canonical available row, and a usable
  response. The test prints only sanitized counts/timings, never API keys,
  prompts, or provider content.
