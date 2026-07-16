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

## Independent-review remediation

Four post-commit review findings were reproduced before fixes:

- guided init called legacy `registry.get()` on `OrderedLLMRoute`, so both init
  endpoints raised `TypeError` instead of probing primary/fallback adapters;
- a model candidate built while an ordinary config writer won could later
  publish its stale whole-config consumer graph;
- CLI and OpenClaw omitted the bundle-owned Embedding service from `SoulEngine`,
  disabling semantic cleanup for manual/avoidance dislikes;
- embedding repair diagnosed its exact provider URL but used Chat-first
  `effective_ollama_endpoint()` for daemon start/restart decisions.

RED evidence captured during remediation:

```text
guided-init ordered-route regressions: 3 failed
live stale-rebase race: 1 failed (pool_target_count reverted 77 -> 20)
CLI/OpenClaw bundle wiring: 2 composition failures
mixed Chat 11434 / Embedding 11435 Ollama management: 5 failed
```

The fixes now probe ordered Chat adapters directly; synchronously restage the
already-built route/service on current live config after canonical reread;
wire one exact bundle-owned Embedding service through CLI/OpenClaw Soul; and
derive every embedding repair management gate from the exact provider root.
General Ollama startup remains an explicit Chat-first, single-managed-daemon
policy, and a recorded endpoint blocks management of another host:port.

Focused GREEN evidence before final whole-repository verification:

```text
init prereqs + both init endpoints: 22 passed
model service + runtime bundle: 94 passed
CLI/OpenClaw embedding identity and cleanup: 4 passed
Ollama supervisor + complete embedding repair class: 84 passed
```

Fresh post-remediation verification:

```text
.venv/bin/pytest tests/test_model_config_service.py \
  tests/test_api_config_transactional.py tests/test_runtime_model_bundle.py -q
106 passed, 30 warnings in 3.36s

.venv/bin/pytest tests/test_runtime_model_bundle.py \
  tests/test_llm_module_routing_e2e.py tests/test_llm_usage.py \
  tests/test_api_app.py tests/test_openclaw_adapter.py tests/test_cli.py -q
647 passed, 1411 warnings in 22.36s

.venv/bin/pytest tests/test_ollama_supervisor.py -q
41 passed, 1 warning in 1.12s

.venv/bin/ruff check src/ tests/
All checks passed!

.venv/bin/mypy src/
Success: no issues found in 221 source files

.venv/bin/pytest -q
5394 passed, 41 skipped, 2776 warnings in 161.23s
```

The required whole-tree formatter exposed six unrelated baseline-only style
hunks. They were restored byte-for-byte to the preceding Task 8 commit so this
remediation does not absorb out-of-scope work. The affected tests and static
checks were then rerun on the final scoped tree:

```text
.venv/bin/pytest tests/test_saved_sync_api.py \
  tests/test_preference_analyzer.py tests/test_github_workflows.py -q
99 passed, 178 warnings in 5.31s

.venv/bin/ruff check src/ tests/
All checks passed!

.venv/bin/mypy src/
Success: no issues found in 221 source files
```
