# vNext API and composition

`/api/v1` is the only public API namespace. Explicit routers expose `system`,
`settings`, `onboarding`, `sources`, `source-tasks`, `events`, `profile`, `feed`,
`interactions`, `library`, `chat`, and `jobs`. All operations have stable
`v1_*` IDs; deterministic OpenAPI lives at `openapi/openapi.json`.

Chat and onboarding/job progress use SSE with typed JSON frames and clean
terminal events. Extension work uses generic lease-safe claim/complete; result
payloads are finite JSON and reject credential-shaped keys.

`api/dependencies.py` owns production composition and `api/v1_models.py` owns
transport-only read schemas. Routers receive an injected
`ApplicationContainer` and call application services; they do not build a DB,
queue, LiteLLM client, or source adapter. Import/app construction makes no live
provider or platform call. `create_app()` only wires lifecycle, middleware,
central errors, feature routers, and static mounts.

Errors map to validation `422`, missing `404`, conflict `409`, unavailable
`503`, and authentication `401/403`, without upstream exception text. `/web`,
`/m`, and `/setup` remain mounted unchanged but keep legacy request wiring until
Task 22.
