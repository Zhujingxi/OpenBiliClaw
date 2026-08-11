# Module Plan 01: Core Runtime

## Outcome

Replace mixed scheduling and lifecycle behavior with a small typed runtime kernel under `src/openbiliclaw/core/`. The Core supervises work but contains no content, recommendation, prompt, provider-authentication, or UI semantics.

## Target package

```text
core/
├── config.py          # immutable validated application settings
├── jobs.py            # JobSpec, schedule and execution policy
├── resources.py       # named concurrency budgets
├── supervisor.py      # task ownership, cancellation, drain
├── health.py          # component/job health snapshots
├── lifecycle.py       # start/reload/stop protocol
└── extensions.py      # typed registration of approved extension kinds
```

Prefer `asyncio.TaskGroup`, `asyncio.timeout`, `asyncio.Semaphore`, `contextlib`, `tomllib`, and the existing scheduler dependency. Do not build custom futures, cancellation tokens, dependency injection, or event buses.

## Public contracts

- `AppSettings`: frozen Pydantic configuration root with typed submodels. Unknown keys fail validation.
- `LifecycleComponent`: typed `start()`, `stop()`, and `health()` protocol. Reload is implemented by replacement, not mutation of arbitrary fields.
- `JobSpec`: stable ID, schedule, timeout, resource class, overlap policy, and callable.
- `RuntimeSupervisor`: owns task creation, cancellation, drain, job status, and exception reporting.
- `ResourceBudget`: named concurrency limit acquired through an async context manager.
- `HealthSnapshot`: immutable structured status, never exception strings containing secrets.
- `ExtensionRegistration`: discriminated union of the explicitly approved extension contracts; no `dict[str, object]` registry.

## Internal phases

### Phase 1 — Configuration and invariants

- Replace the mutable/global portions of `config.py` with frozen nested Pydantic models and a small `tomllib` loader.
- Separate model, access, content-provider, recommendation, host, and runtime settings.
- Define deterministic precedence for file, environment, and CLI override values.
- Reject unknown fields and invalid cross-field combinations before constructing components.
- Keep secrets out of `AppSettings`; settings contain only secret references.
- Add redacted serialization for diagnostics and `config-show`.

### Phase 2 — Resource and task supervision

- Introduce one supervisor as the only owner of application-created background tasks.
- Convert unowned `asyncio.create_task` usage to supervisor calls.
- Implement bounded job execution, timeout propagation, overlap rejection, cancellation, and drain.
- Preserve `CancelledError`; never catch and convert it to a normal failure.
- Record start/end/error/timeout metrics using typed events.
- Unit-test cancellation during startup, normal work, timeout, reload, and shutdown.

### Phase 3 — Scheduled jobs

- Represent proactive work as registered `JobSpec` values.
- Use the scheduler only to trigger supervisor-owned jobs; scheduler callbacks contain no product logic.
- Define explicit missed-run and overlap behavior per job.
- Expose job health and last-result metadata without exposing payload content.
- Remove platform-specific producer cadence decisions from the Core; those belong to Discovery & Recommendation.

### Phase 4 — Lifecycle and reload

- Define ordered startup and reverse-order shutdown for concrete lifecycle components supplied by Composition.
- Implement replacement-based configuration reload: validate and build a new component set, then atomically swap after readiness succeeds.
- Drain old components with a deadline; failed replacement leaves the old graph active.
- Add degraded-start behavior only for explicitly optional components.
- Test partial startup failure and ensure no task, socket, scheduler, or database handle leaks.

### Phase 5 — Extension registration and cleanup

- Register only typed extension categories listed in the architecture.
- Reject duplicate IDs and incompatible capability versions at startup.
- Ensure extension jobs pass through normal budgets and health reporting.
- Move reusable supervision behavior out of mixed files under `runtime/`.
- Leave product job definitions in their owning modules.

## Current code disposition

- Split lifecycle/resource logic from `runtime/refresh.py`, `runtime/api_server.py`, `runtime/init_*`, and `api/runtime_context.py`.
- Retain platform autostart adapters only as host/infrastructure adapters; they are not Core scheduling.
- Do not move producer, recommendation, embedding, or dialogue logic into `core/`.
- `RuntimeContext` is deleted during Runtime Composition cutover.

## Tests and quality gates

- Fake clock/scheduler tests; no sleeping in unit tests.
- Task leak test checks the event loop after shutdown.
- Property-style table tests cover all overlap and missed-run policies without adding a property-testing dependency.
- MyPy proves all job callables and lifecycle components are typed.
- Architecture test prevents `core/` from importing product modules.

## Documentation updates during implementation

Update `docs/modules/runtime.md`, `docs/modules/config.md`, `docs/architecture.md`, `docs/spec.md`, README diagrams, and `docs/changelog.md` as behavior lands.

## Completion criteria

- Every application background task has an owner, timeout policy, cancellation path, resource class, and health record.
- Core imports no provider or product package.
- Configuration validation completes before side effects.
- Reload is atomic from the caller's perspective.
- No custom orchestration framework or untyped registry exists.
