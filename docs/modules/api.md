# API Host

The current FastAPI host is `openbiliclaw.hosts.api`. It exposes strict `/v1` transport schemas over the same Application workflows used by composition.

## Routes

- `/v1/sources` and source form/connect/disconnect
- `/v1/recommendations` and supervised refresh
- `/v1/feedback`, `/v1/observations`
- `/v1/profiles/{profile_id}` and profile edit
- `/v1/content/search`, content details, propose/confirm actions
- `/v1/assistant/turns`, scoped conversation reads
- `/v1/runtime/health`
- replayable event stream routes
- `/v1/openapi.json`

Mutations require matching `X-Device-ID` and `X-CSRF-Token`. The host enforces body, timeout, rate, origin, bearer, and websocket subscriber bounds. Non-loopback binding requires authentication policy. The Vue SPA is served only for non-API GET fallback paths.
