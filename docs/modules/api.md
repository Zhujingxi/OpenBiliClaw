# API Host

The current FastAPI host is `openbiliclaw.hosts.api`. It exposes strict `/v1` transport schemas over the same Application workflows used by composition.

## Routes

- `/v1/sources` and source form/connect/disconnect; connect accepts the provider form's secret `submission` mapping unchanged (for example the Bilibili `cookie` field), rather than inventing a generic credential field
- `/v1/recommendations` delivers selected items, marks them shown, and returns a stable `shown_id`; supervised refresh remains a separate bounded mutation
- `/v1/feedback` accepts `idempotency_key`, delivered `shown_id`, matching `content_ref`, and a feedback kind (`liked`/`dismissed` for the Web controls); it transitions the recommendation to interacted and atomically records its learning observation. Unknown shown IDs return the typed `not_found` envelope
- `/v1/observations` accepts explicit typed observation batches
- `/v1/profiles/{profile_id}` and profile edit
- `/v1/content/search`, content details, propose/confirm actions
- `/v1/assistant/turns`, scoped conversation reads
- `/v1/runtime/health`
- replayable event stream routes
- `/v1/openapi.json`

Mutations require matching `X-Device-ID` and `X-CSRF-Token`. Loopback binding may intentionally run without a bearer token; non-loopback binding requires a bearer resolved from the credential vault through `host.bearer_secret_ref`. When configured, the bearer protects API routes and the served SPA fallback alike. The host enforces body, timeout, rate, origin, bearer, and websocket subscriber bounds and returns typed JSON error envelopes rather than HTML. The Vue SPA is served only for authenticated non-API GET fallback paths. SPA HTML fallback responses use `Cache-Control: no-cache`; fingerprinted `/assets/*` files use `public, max-age=31536000, immutable`.
