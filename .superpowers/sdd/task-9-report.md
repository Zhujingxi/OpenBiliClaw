# Task 9 Delivery Report

## Status

Complete after review remediation. The branch exposes the dedicated,
secret-safe model configuration API, protects native `[models]` from legacy
`/api/config` writers, retains only the outbound-network probe on the legacy
probe endpoint, and now closes the probe/lifecycle/init races found during
review.

## Delivered scope

- Added strict request/response models for ordered Chat connections, shared
  Embedding settings and providers, explicit `keep/set/clear/env` credential
  actions, migration resolutions, public credential state, exact probe state,
  live circuit state, and descriptor registry output. Unknown fields and type
  coercion are rejected; response schemas contain no raw credential field.
- Added `GET/PUT /api/model-config`, `GET /api/model-connection-types`, and
  `POST /api/model-config/probe` through a route installer around
  `ModelConfigService`. Saves preserve the service's revision/authority guard,
  fieldized validation, transactional persistence/runtime swap, rollback, and
  single `config_reloaded` publisher.
- Exact probes accept one Chat draft or one Embedding provider plus the full
  shared settings object. They do not persist or fallback and continue to use
  the runtime maintenance concurrency gate. A `keep` action resolves only the
  same stable ID at the current revision.
- Added in-memory, secret-free exact-probe summaries with UTC timestamps and
  source revision. GET history follows stable IDs through pure reorder but is
  invalidated by record edits, credential transitions, unexplained revision
  changes, or any shared Embedding setting change. An edited/unsaved same-ID
  draft receives its POST result without evicting the persisted record's last
  exact summary.
- Added `RuntimeContext.record_model_probe_success()`. Only a successful probe
  whose full draft fingerprint matches the current persisted record can close
  the matching live circuit for the same ID, capability, and current revision;
  peers, old revisions, brand-new IDs, edited drafts, and Embedding drafts with
  different shared settings remain untouched.
- Made legacy `GET /api/config` a credential-free, non-authoritative,
  read-only `primary_and_first_fallback` projection. It never reveals model
  keys, including with `reveal_keys=true`, never substitutes later fallbacks,
  and never collapses duplicate connection types over one provider bucket.
- When native `[models]` is active, legacy `PUT /api/config` ignores `llm` and
  `llm.*` reset attempts, preserves the native route, returns
  `model_config_not_updated`, and continues saving unrelated general settings.
  The old `/api/config/probe-service` now accepts only `network_proxy`; its
  unreachable model probe implementations were removed.
- Kept all four model endpoints reachable in degraded mode and guarded model
  saves/probes while guided initialization is active.
- Bound every HTTP exact probe to one captured revision. A `keep` credential is
  resolved while holding the short model path lock, the network call runs after
  releasing that lock, and completion revalidates the same revision before any
  history or live-circuit effect. A stale probe returns the latest snapshot with
  no side effect. `ModelConfigService.probe()` remains only as non-HTTP legacy
  compatibility for callers that do not attach history/circuit effects.
- Added an app-owned model lifecycle coordinator. A successful save now
  publishes the complete graph without an event, drains every registry-owned
  old-graph task except `guided_init`, restarts app loops from the new graph,
  clears runtime/app degraded state, then publishes exactly one
  `config_reloaded`. A completed exceptional child is cleanup data rather than
  a cutover failure, while cancellation of the save caller still propagates and
  enters rollback. Failure or cancellation restores the old bytes and exact
  normal/degraded runtime graph, then recreates old-equivalent app loops
  according to the previous ownership; it does not claim to preserve the same
  `asyncio.Task` objects or recreate cancelled detached old-graph one-shots.
- Moved guided-init reservation into the canonical config writer and added a
  model-save precommit guard inside that same writer. A model commit and init
  reservation therefore cannot cross after their handler-level checks. Probes
  recheck init after maintenance-gate admission and again immediately after
  acquiring the model path lock, before disk/credential capture. A probe queued
  behind a slow save therefore cannot cross a later init reservation; network
  work remains outside the lock.
- Made a successful dedicated model save a complete degraded-mode recovery:
  full consumer graph, background loops, both degraded flags, and the reload
  event all transition in the lifecycle order above without requiring restart.
- Updated configuration/runtime module docs, changelog, index, architecture,
  spec, and bilingual README architecture diagrams. The graphical list/detail
  editors and CLI editor remain explicitly assigned to later tasks.

## TDD evidence

- Initial Task 9 RED command:
  `pytest tests/test_api_model_config.py tests/test_api_config_guards.py tests/test_api_config_probe.py tests/test_api_config_transactional.py -q`
  produced `15 failed, 56 passed`: the model API module/routes were absent,
  legacy native writes were unguarded, and the legacy endpoint still accepted
  model probes.
- Shared Embedding probe identity RED produced two failures: an unsaved settings
  change incorrectly closed the live circuit, and a settings-only PUT retained
  stale probe history. Adding all four shared settings to the fingerprint made
  both regressions GREEN.
- Persisted-history RED showed a same-ID edited draft evicting the current exact
  summary. GET-visible history is now updated only for an exact persisted
  fingerprint, while POST still reports the edited draft result.
- The legacy migration API fixture now generates a real unrouted credential
  issue and verifies its tuple-backed `allowed_actions` survives strict JSON
  output as a non-empty list without exposing either legacy secret.
- Probe-race RED used two deterministic barriers: a gate-waiting revision-A
  request and an in-flight revision-A network call both returned `200` before
  remediation after revision B changed only the secret. They now return `409`,
  never borrow B's secret, and never update B history/circuit state.
- Lifecycle RED showed only `config_reloaded` with no background-task restart;
  an injected restart failure also returned `200`. The production-app
  regression now proves `restart -> event`, while failure restores disk and the
  exact old consumer graph, restarts old-equivalent app-loop ownership, and emits no
  success event.
- The final lifecycle review REDs covered three cleanup edges: an already-done
  exceptional app slot aborted cutover, a registry-owned detached old-graph
  task was still alive when the reload event fired, and cancellation of the
  save caller was swallowed while a child handled cancellation. The fixed
  lifecycle clears all slots before awaiting, treats child outcomes as cleanup
  results, drains the registry except `guided_init`, and preserves caller
  cancellation through rollback. Rollback recreates only old-equivalent app
  loops; detached old one-shots remain cancelled.
- Init-race RED showed a save completing with `200` after init became active
  during candidate construction, and `try_start()` occurring outside the
  canonical writer. Both deterministic regressions now return/observe the
  guarded behavior. A separate gate barrier verifies a probe performs no
  credential or network work when init starts while it waits.
- A second deterministic probe barrier held a slow save inside the model path
  lock, admitted the probe through its maintenance gate, then activated init
  while the probe waited for that lock. RED returned `200` and reached
  credential/network work; GREEN returns the same safe `409 init_running` as
  the outer guard and performs neither operation.
- Broad verification found one compatibility edge after the cleanup rewrite:
  short-lived `TestClient` requests can leave a completed app slot attached to
  an already-closed event loop, and `asyncio.gather()` rejects even a completed
  foreign-loop future. The final implementation consumes completed outcomes
  directly and gathers only unfinished tasks; both existing repeated-save
  regressions pass without weakening same-loop cancellation semantics.
- A degraded-runtime integration regression forces initial registry failure,
  repairs through `PUT /api/model-config`, and verifies task restart precedes
  the single event, both degraded flags clear, the full graph exists, and a
  formerly guarded API returns `200`.

## Verification

- Final reviewer-blocker regression set: `4 passed` in 1.25s.
- Closed-loop repeated-save compatibility reproductions: `2 passed` in focused
  isolated runs.
- Required Task 9 matrix: `90 passed` in 6.54s.
- Model API, production app, and degraded compatibility set: `446 passed` in
  19.40s.
- Model domain, service, connection factory, ordered Chat/Embedding route, and
  runtime bundle set: `460 passed` in 1.40s.
- Background task registry set: `7 passed` in 0.75s.
- Full repository suite: `5,430 passed, 41 skipped` in 145.52s.
- MyPy strict check: success across 223 source files.
- Repository-wide Ruff lint: passed.
- All four touched Python files pass Ruff format check.
- `git diff --check`: passed.
- Fresh temporary-root `openbiliclaw config-show`: passed and displayed only
  default/redacted model state without starting the server.

## Explicit boundaries

- Probe history is process-local UI status, not persisted configuration.
- Model API writer coordination remains in-process; the model service's
  documented narrow external-writer race and lack of a cross-process lock are
  unchanged.
- Task 9 supplies the backend contract only. Desktop, extension, and mobile
  list/detail editors and the unified CLI model editor are later plan tasks.
