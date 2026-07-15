# Task 8 — production runtime model bundle report

## Scope

Implemented Task 8 only: production runtime, CLI, OpenClaw, health and Ollama
composition now read `Config.models` and use one global ordered Chat route plus
one shared-settings ordered Embedding route. Model editing API/UI work remains
outside this task.

## RED evidence

The first focused run after adding Task 8 regressions was:

```text
.venv/bin/pytest tests/test_runtime_model_bundle.py \
  tests/test_llm_module_routing_e2e.py tests/test_llm_usage.py -q
```

Observed before implementation: **10 failed, 19 passed**. The failures proved
the runtime bundle did not exist, production still used legacy/module routing,
and the usage ledger lacked connection columns, migration/index behavior,
metadata persistence and connection-aware pricing/reporting.

## Implemented behavior

- `RuntimeModelBundle` is immutable and requires `revision`, typed `models`,
  `chat_route`, `llm_service`, and optional `embedding_service`.
- `build_runtime_model_bundle()` constructs every Chat/Embedding adapter,
  ordered route, usage recorder and service before returning a bundle.
- `RuntimeContext` owns one current bundle and one swap lock. A complete
  downstream consumer graph is staged before publication; the short
  publication section contains no `await`.
- Old in-flight calls keep their captured old route. New calls observe the new
  bundle only after publication.
- The stable `LLMConcurrencyGate` reads only `models.chat.concurrency` and is
  validated/configured immediately before successful publication. A gate or
  late consumer failure leaves the previous graph, capacity and inventory
  untouched.
- Task 7 hooks now restore the exact prior bundle and exact consumer identities.
  A successful swap emits `config_reloaded` with the new revision; rollback
  emits no success event.
- Runtime, CLI and OpenClaw share the same native composition. Soul, Dialogue,
  Discovery and Recommendation no longer accept module override data.
- `llm_usage` persists `connection_id`, `connection_type`, `preset` and
  `route_position`; old databases receive idempotent columns plus
  `idx_llm_usage_connection_timestamp`. Provider aggregates remain available,
  while `openbiliclaw cost` adds a connection view.

## Intentional handoff boundaries

- Legacy `/api/config` projection/mutation remains Task 9. Compatibility code
  may still read/write the old API shape, but production model consumers use
  `Config.models`.
- CLI legacy setup/save writers remain Task 14. CLI runtime commands,
  `config-show`, `health-check` and `cost` use the native ordered model view.
- `_migration_mapping.py` still recognizes legacy module override tables only
  to produce a migration report/resolution. No production service consumes
  those values.

## Focused GREEN evidence

```text
.venv/bin/pytest tests/test_llm_usage.py -q
25 passed

.venv/bin/pytest tests/test_runtime_model_bundle.py \
  tests/test_llm_module_routing_e2e.py -q
4 passed

.venv/bin/pytest tests/test_api_app.py -q
389 passed

.venv/bin/pytest tests/test_cli.py tests/test_openclaw_adapter.py \
  tests/test_soul_engine.py tests/test_soul_dialogue.py tests/test_llm_service.py \
  tests/test_ollama_supervisor.py tests/test_refill_e2e_contract.py \
  tests/test_api_config_transactional.py tests/test_runtime_model_bundle.py \
  tests/test_llm_module_routing_e2e.py tests/test_llm_usage.py \
  tests/test_llm_concurrency.py -q
489 passed
```

The runtime-bundle focused count above preceded the atomic gate-publication
regression added during review; the final verification section records the
fresh post-format run.

## Final verification

The first full-suite run exposed tests that still instantiated partial
`SimpleNamespace` configs, mutated only `Config.llm`, or monkeypatched the
retired registry/rebuild seams:

```text
.venv/bin/pytest -q
21 failed, 5343 passed, 41 skipped, 2676 warnings, 19 errors in 192.25s
```

Those fixtures were migrated to complete native `Config.models` routes, native
adapter builders, and the staged-build/short-publication contract. The config
secret-reset regression still clears an allowlisted inactive credential; an
active-route credential reset remains rejected because it would make the
candidate unbuildable. The repaired failure group then passed together:

```text
.venv/bin/pytest tests/test_api_config_guards.py \
  tests/test_api_config_probe.py tests/test_api_reddit_tasks.py \
  tests/test_api_xhs_ingest.py tests/test_x_creators.py \
  tests/test_xhs_tasks.py tests/test_e2e_multi_source_diversity.py \
  tests/test_saved_sync_api.py -q
167 passed, 514 warnings in 11.13s
```

Fresh final checks after all implementation and fixture changes:

```text
.venv/bin/pytest tests/test_runtime_model_bundle.py \
  tests/test_llm_module_routing_e2e.py tests/test_llm_usage.py \
  tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_cli.py -q
640 passed, 1391 warnings in 28.83s

.venv/bin/ruff check src/ tests/
All checks passed!

.venv/bin/mypy src/
Success: no issues found in 221 source files

.venv/bin/pytest -q
5383 passed, 41 skipped, 2756 warnings in 177.35s
```
