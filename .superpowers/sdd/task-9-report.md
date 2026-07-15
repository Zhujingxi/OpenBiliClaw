# Task 9 Delivery Report

## Status

Complete. The branch now exposes the dedicated, secret-safe model
configuration API, protects native `[models]` from legacy `/api/config`
writers, and retains only the outbound-network probe on the legacy probe
endpoint.

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

## Verification

- Required Task 9 matrix: `80 passed` in 5.89s.
- Task 9 plus broad API/degraded compatibility set: `487 passed` in 34.18s.
- Model domain, service, ordered Chat/Embedding route, and runtime bundle set:
  `339 passed` in 1.61s.
- Full repository suite: `5,418 passed, 41 skipped` in 146.87s.
- MyPy strict check: success across 223 source files.
- Repository-wide Ruff lint: passed.
- Every touched Python file passes Ruff format check. The repository-wide
  format check still reports six pre-existing unrelated files
  (`saved_sync/extension_broker.py`, `saved_sync/service.py`,
  `soul/preference_analyzer.py`, and three corresponding test files); Task 9
  did not rewrite them.
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
