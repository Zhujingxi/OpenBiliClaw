# Module Plan 15: Runtime Composition and Final Cutover

## Outcome

Create `src/openbiliclaw/composition/` as the only package that knows every concrete implementation. It builds one typed production graph, gives it to Core and the hosts, performs atomic reload, and removes all legacy wiring and duplicate implementations.

## Target package

```text
composition/
├── application.py       # frozen concrete application graph
├── build.py             # pure/side-effect-minimized constructors
├── providers.py         # explicit first-party provider construction
├── repositories.py      # concrete repository assembly
├── lifecycle.py         # startup/shutdown ordering
├── reload.py            # validate/build/readiness/swap/drain
└── entrypoints.py       # CLI/server process entrypoints
```

## Composition model

Use a typed frozen `Application` dataclass containing the small number of top-level services and host dependencies. Do not expose it as a service locator to product modules. Constructors receive explicit dependencies; tests construct smaller graphs directly.

## Internal phases

### Phase 1 — Construction inventory

- Enumerate every constructor and side effect currently in `api/runtime_context.py`, `api/app.py`, `cli.py`, and mixed runtime helpers.
- Assign each side effect to Core lifecycle, Infrastructure, a product module, a host, or Composition.
- Reject unowned global initialization.
- Define startup dependency order and reverse shutdown order.

### Phase 2 — Repository and infrastructure graph

- Build validated settings, telemetry, SQLite database, concrete repositories, credential vault, HTTP clients, and event transport.
- Pass concrete repositories only to owning services/workflows.
- Keep constructors side-effect-free where possible; open resources only during lifecycle start.
- Ensure partial construction/startup can close every created resource.

### Phase 3 — AI, access, and content graph

- Build model/embedding providers and AI Runtime routes.
- Build access methods and provider-specific auth adapters.
- Construct first-party content providers explicitly and register validated manifests.
- Fail startup for invalid required components; expose optional provider degradation without breaking unrelated providers.

### Phase 4 — Product and host graph

- Build Observation Ingress, User Understanding, Discovery & Recommendation, Application Workflows, and Assistant in dependency order.
- Build Core job specifications from product-owned job factories.
- Build FastAPI/Typer host adapters from a narrow application facade.
- Mount built frontend assets without coupling frontend build logic to domain services.

### Phase 5 — Lifecycle and atomic reload

- Start database/infrastructure before dependent services, then Core jobs, then hosts.
- Validate replacement configuration, construct a complete new graph, run readiness checks, and atomically swap host/application references.
- Drain and close the old graph after the swap.
- If replacement fails, close the candidate graph and keep the active graph unchanged.
- Test reload during active Assistant requests, provider calls, recommendation jobs, and websocket connections.

### Phase 6 — Entry points and packaging

- Point `openbiliclaw` CLI and server startup to Composition entrypoints.
- Update Python package data/build hooks for generated web assets without committing build output.
- Keep extension packaging separate from backend process startup.
- Update Docker/install/autostart commands to invoke one supported entrypoint.
- Remove optional dependencies no longer used and pin new runtime/frontend build dependencies.

### Phase 7 — Final cutover and deletion audit

- Switch every production host and scheduled job to the new graph once repository-wide tests pass.
- Delete `RuntimeContext`, old component builders, custom LLM stack, source adapters/task dispatch, soul/memory engines, old discovery/recommendation engines, database/API/CLI monoliths, and legacy web JavaScript according to the ledger.
- Delete tests that assert removed interfaces; preserve behavior coverage through target contracts.
- Run import, dependency, dead-file, configuration-key, route, CLI-command, database-table, and frontend-asset audits.
- Confirm there is exactly one implementation for each responsibility.

### Phase 8 — Release verification

- Test fresh install/start/configure/connect/understand/discover/recommend/feedback/dialogue/shutdown flows.
- Test upgrade behavior as explicitly documented: backup and target-schema reset/import decision, not transparent compatibility.
- Run opt-in provider/model smokes with redacted fixtures and no committed credentials.
- Verify desktop, mobile, extension popup, CLI, and API surfaces.
- Update all mandatory current-state documentation and release/checklist artifacts.

## Required end-to-end scenarios

1. Fresh local startup with no credentials and no model provider.
2. Configure model, verify capability route, and run a typed analyzer.
3. Connect an anonymous provider and a manual-secret provider without secret exposure.
4. Record feedback, consume observation, update understanding, and refresh recommendations.
5. Produce and read proactive recommendations while Assistant is unavailable.
6. Search through Assistant native tools with bounded provider access.
7. Confirm a mutation action and reject expired/replayed actions.
8. Reload valid configuration atomically and survive invalid replacement configuration.
9. Cancel long-running provider/model work and shut down without leaked tasks/resources.
10. Render known and unknown provider cards across desktop, mobile, and extension shells.

## Tests and quality gates

- Run every global gate in `README.md` from a clean checkout.
- Add end-to-end tests at stable host/application boundaries; avoid private-method assertions.
- Inspect active tasks, database handles, HTTP clients, schedulers, and sockets after shutdown.
- Generate OpenAPI and frontend artifacts twice and assert reproducibility.
- Run a repository scan for forbidden legacy imports, files, JavaScript sources, config keys, and dependencies.
- Perform a security review of credentials, prompt injection, host exposure, and external mutations.

## Documentation updates during implementation

Complete every item in `CLAUDE.md`'s documentation checklist, including module docs, changelog, current architecture/spec diagrams, README/README_EN diagrams, API/CLI/config docs, installer/deployment docs, and release highlights when applicable.

## Completion criteria

- One explicit production component graph exists and is owned by Composition.
- Startup, reload, drain, and shutdown are deterministic and leak-free.
- No service locator or god runtime context remains.
- Every legacy item in the disposition ledger is removed or explicitly documented as retained target behavior with one owner.
- Python and TypeScript quality gates, integration tests, security tests, and documentation checks pass from a clean checkout.
