# vNext API and composition

`/api/v1` is the only public API namespace. Explicit routers expose `system`,
`settings`, `onboarding`, `sources`, `source-tasks`, `events`, `profile`, `feed`,
`interactions`, `library`, `chat`, and `jobs`. All operations have stable
`v1_*` IDs; deterministic OpenAPI lives at `openapi/openapi.json`.

Chat and onboarding/job progress use SSE with typed JSON frames and clean
terminal events. Extension work uses generic lease-safe claim/complete; result
payloads are finite JSON and reject credential-shaped keys. Synchronous claim,
job inspection, and chat-persistence ports run through bounded AnyIO worker
threads, so slow SQLite work does not stall the ASGI event loop; cancellation
waits for transaction/lease side effects instead of abandoning them.

Public job scheduling accepts only the named `interactive`, `user-triggered`,
and `scheduled` lanes. Settings source enable/weight objects are partial patches
merged into the complete seven-source maps. `onboarding_complete` is read-only
over the public settings route and is advanced only by the onboarding workflow.

`api/dependencies.py` owns production composition and `api/v1_models.py` owns
transport-only read schemas. Routers receive an injected
`ApplicationContainer` and call application services; they do not build a DB,
queue, LiteLLM client, or source adapter. Import/app construction makes no live
provider or platform call. `create_app()` only wires lifecycle, middleware,
central errors, feature routers, and static mounts.
Shutdown cleanup also runs when startup fails, while preserving the original
startup error and keeping cleanup exception details out of logs.

Errors map to validation `422`, missing `404`, conflict `409`, unavailable
`503`, and authentication `401/403`, without upstream exception text. `/web`,
`/m`, and `/setup` remain mounted unchanged but keep legacy request wiring until
Task 22.
