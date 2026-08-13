# API Host

The current FastAPI host is `openbiliclaw.hosts.api`. It exposes strict `/v1` transport schemas over the same Application workflows used by composition.

## Routes

- `/v1/sources` and source form/connect/disconnect
- `/v1/recommendations` delivers selected items, marks them shown, and returns a stable `shown_id`; supervised refresh remains a separate bounded mutation
- `/v1/feedback` requires that delivered `shown_id` plus the matching content reference, transitions the recommendation to interacted, and atomically records its learning observation; unknown shown IDs return the typed `not_found` envelope
- `/v1/observations` accepts explicit typed observation batches
- `/v1/profiles/{profile_id}` and profile edit
- `/v1/content/search`, content details, propose/confirm actions
- `/v1/assistant/turns`, scoped conversation reads
- `/v1/runtime/health`
- replayable event stream routes
- `/v1/openapi.json`

Mutations require matching `X-Device-ID` and `X-CSRF-Token`. Loopback binding may intentionally run without a bearer token; non-loopback binding requires authentication policy. The host enforces body, timeout, rate, origin, bearer, and websocket subscriber bounds and returns typed JSON error envelopes rather than HTML. The Vue SPA is served only for non-API GET fallback paths.
