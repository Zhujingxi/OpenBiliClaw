# Runtime Composition

> Landed scope: Module Plan 15 phases 1–6. The v2 graph is opt-in; legacy production cutover and deletion are deliberately deferred to Phase 15b.

## Purpose

`openbiliclaw.composition` is the only package allowed to know all concrete target implementations. It validates configuration, constructs one frozen `Application`, owns resource lifecycle, and atomically reloads complete graphs. Product modules receive narrow constructor dependencies and never import Composition.

## Construction inventory

| Legacy construction site / side effect | Current owner | Phase 15 graph assignment |
|---|---|---|
| `api/runtime_context.py`: config/database/provider/model/service construction and cached global context | Composition | `build.py`, `repositories.py`, `providers.py`; no global context |
| `api/app.py`: FastAPI construction, background task startup, websocket/event delivery, frontend mount | API Host / Composition | Host retains transport; Composition supplies typed dependencies and lifecycle |
| `cli.py`: config/database/service construction and process commands | CLI Host / Composition | Host retains Typer adaptation; `composition.entrypoints` builds the graph |
| `runtime/init_coordinator.py`, `runtime/init_prereqs.py`: readiness and initialization sequencing | Composition | staged `LifecyclePlan` readiness |
| `runtime/refresh.py`, `runtime/task_registry.py`: task admission and cancellation | Core | `RuntimeSupervisor` / product `JobSpec` factories; wired by Composition |
| SQLite file creation, migrations, connection executor | Infrastructure | Constructed dormant; migration/open occur in lifecycle start |
| credential backend selection and protected directory creation | Infrastructure / Composition | backend selected by Composition; secret access remains Vault-owned |
| HTTP client creation | Infrastructure / providers | factories/transports are constructed dormant and close through lifecycle |
| provider manifest registration | Content Integration / Composition | explicit allowlist in `providers.py`; registry validates advertised capabilities |
| product repository and service constructors | Owning product module / Composition | concrete repositories assembled once and passed only to owners |
| API/CLI object factories and frontend mount | Hosts | graph contains narrow host dependencies; production cutover is Phase 15b |

No module-level graph, database connection, HTTP client, scheduler, task, or socket is created. Calling `build_application()` does not open the database. The protected credential directory is the only constructor-time filesystem safety operation inherited from Infrastructure; database/schema creation happens at start.

## Graph and public API

- `validated_settings(path, environ, overrides) -> AppSettings`
- `build_application(settings, options) -> Application`
- `build_providers(enabled) -> ProviderGraph`
- `Application.start()`, `Application.stop()`, `Application.ready()`
- `ApplicationReference.lease()` for in-flight request/provider/job/websocket ownership
- `reload_application(...)` for candidate build/readiness/swap/drain
- `openbiliclaw-v2 check [--config PATH] [--data-dir PATH]`
- `openbiliclaw-v2 serve [--config PATH] [--data-dir PATH]`
- `python -m openbiliclaw.composition.entrypoints check|serve`

`Application` is frozen and contains only settings, top-level product services, infrastructure ownership, provider/repository graph metadata, lifecycle, and host dependencies. It is not passed into product modules and is not a service locator.

## Startup and shutdown

Startup is stable-sort ordered:

1. **Infrastructure** — schema migration, SQLite open, event transport, HTTP ownership.
2. **Services** — provider readiness and product services.
3. **Core jobs** — supervised product-owned `JobSpec` registrations (cutover wiring in 15b).
4. **Hosts** — API/CLI process adapters (cutover wiring in 15b).

Shutdown executes the exact reverse order. A required start/readiness failure stops the failing component and rolls back every started component. Optional provider unavailability marks the lifecycle degraded without preventing unrelated providers or infrastructure from starting.

## Atomic reload

A candidate uses already-validated `AppSettings`, is fully constructed, started, and readiness-checked before publication. Failure closes the candidate and leaves the active reference unchanged. Success atomically swaps the reference; existing `lease()` holders keep the old graph while new work sees the candidate. The old graph drains to a deadline and then stops. Leases cover the same ownership shape for Assistant requests, provider calls, recommendation jobs, and websocket connections.

## Security and data

Composition never serializes vault contents or gives the Vault to Assistant/product facades. Settings contain opaque references only. Existing schema migration rules still reject unversioned/destructive changes without explicit backup/reset/import decisions.

## Deferred Phase 15b work

The legacy `openbiliclaw` command and legacy API runtime remain unchanged. `openbiliclaw-v2 serve` starts the composed `/v1` read/observation host; optional mutation, Assistant/model routes, and proactive jobs fail explicitly until their production adapters are cut over. Phase 15b owns the legacy command cutover, remaining workflow wiring, disposition-ledger deletion, installer/Docker command migration, and full release verification. Card scenario 10 remains covered by existing Vitest presentation tests.
