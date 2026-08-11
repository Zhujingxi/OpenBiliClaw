# Module Plan 13: API and CLI Hosts

## Outcome

Replace the monolithic FastAPI and Typer surfaces with thin typed host adapters under `src/openbiliclaw/hosts/`. Hosts validate transport input, authenticate, call Application Workflows or Assistant, and translate typed results. They contain no domain sequencing.

## Target package

```text
hosts/
├── api/
│   ├── app.py             # FastAPI factory and middleware only
│   ├── dependencies.py    # typed auth/application dependencies
│   ├── errors.py
│   ├── schemas/           # transport-only Pydantic models
│   └── routers/
│       ├── sources.py
│       ├── recommendations.py
│       ├── understanding.py
│       ├── assistant.py
│       ├── content.py
│       ├── runtime.py
│       └── events.py
└── cli/
    ├── app.py
    └── commands/
```

## Host rules

- FastAPI routers depend on a typed application facade supplied by Composition.
- HTTP schemas are distinct from domain models when transport/versioning concerns differ.
- CLI commands call the same workflows as HTTP routes.
- No route or command reads SQLite, credentials, content-provider clients, or AI providers directly.
- OpenAPI is the source for generated TypeScript API types.

## Internal phases

### Phase 1 — Surface inventory and contract reset

- Inventory current routes, websocket messages, CLI commands, and extension endpoints.
- Keep only target product operations and diagnostics.
- Define a clean versioned HTTP namespace and consistent error envelope; do not preserve old routes.
- Define authentication, local-network exposure, CSRF/origin, and device identity policy.
- Map each endpoint and command to exactly one workflow or Assistant operation.

### Phase 2 — Typed schemas and error mapping

- Define Pydantic request/response models with strict validation and discriminated unions.
- Ban response dictionaries assembled ad hoc.
- Map domain/application errors centrally to stable status codes and safe messages.
- Distinguish validation, unauthorized, forbidden, unavailable capability, conflict, rate limit, and temporary failure.
- Ensure secrets are write-only fields excluded from schema examples and response models.

### Phase 3 — FastAPI router split

- Build a small app factory with middleware, lifespan integration, and router registration only.
- Implement routers by product capability.
- Use cursor pagination and explicit limits.
- Implement typed server-sent/websocket event envelopes for job, recommendation, assistant, and connection status updates.
- Keep event replay/reconnect policy explicit and bounded.

### Phase 4 — CLI split

- Keep Typer commands as formatting/input adapters over workflows.
- Preserve only valuable commands: start/status/config diagnostics/provider connect/profile/recommend/model diagnostics as target behavior requires.
- Make command output structured internally and render with Rich at the edge.
- Ensure noninteractive commands have deterministic exit codes and no hidden prompts.

### Phase 5 — OpenAPI/TypeScript contract generation

- Export deterministic OpenAPI from the app factory without starting external services.
- Generate TypeScript types/client inputs into `frontend/packages/api-client/`.
- Fail CI when generated output differs.
- Add schema snapshot tests for all frontend-consumed endpoints.
- Keep generated files clearly marked and never edit them manually.

### Phase 6 — Security and cleanup

- Enforce bind-address/auth policy before server startup.
- Add request/body size, timeout, origin, CSRF, rate, and websocket subscriber limits.
- Redact request/error logs.
- Delete `api/app.py`, `api/models.py`, legacy source-auth routes, monolithic `cli.py`, and duplicated host helpers after cutover.
- Move static-file serving to the built frontend artifact adapter only.

## Tests and quality gates

- FastAPI tests call the ASGI app with typed fake application dependencies.
- Every endpoint has success, validation, auth, conflict, and safe-error coverage as relevant.
- CLI tests assert exit code and structured behavior.
- OpenAPI generation is deterministic and contains no secret response fields.
- Websocket/reconnect tests use bounded virtual/test time.
- MyPy checks dependencies and schemas without untyped request state access.

## Documentation updates during implementation

Update `docs/modules/api.md`, `docs/modules/api-auth.md`, `docs/modules/cli.md`, configuration docs, generated API reference, architecture diagrams, install/deployment docs, and `docs/changelog.md`.

## Completion criteria

- API and CLI are thin adapters over one application contract.
- No monolithic app, models, or CLI file remains.
- OpenAPI is complete enough to generate the TypeScript client.
- Secrets are write-only and absent from all responses and examples.
- Host security and resource limits are explicit and tested.
