# vNext API access control

The vNext API uses one installer-generated bearer token from
`OPENBILICLAW_ACCESS_TOKEN`. It has no source default, is compared in constant
time, and is absent from OpenAPI.

```http
Authorization: Bearer <local instance token>
```

Missing/malformed authentication returns `401`; a wrong token returns `403`.
Errors never echo the submitted header. `GET /api/v1/system/readiness` is
public. Onboarding is public only while `onboarding_complete=false`; afterward
it requires the same token. Legacy password, token-exchange, WebSocket-query,
and trusted-local bypass routes are not part of the authoritative app.

Installers create the token outside Git. Web/extension token wiring lands in
Task 22; their static assets remain mounted but are not yet vNext-wired.
