# vNext API access control

`/api/v1` supports three coexisting, secret-separated authentication mechanisms:

- the installer bearer from `OPENBILICLAW_ACCESS_TOKEN` for operational/API clients;
- an HttpOnly `obc_session` cookie for the same-origin Web UI after password login;
- a finite bearer session for the browser extension, exchanged from a device key.

`GET /api/v1/auth/status` is public and returns only readiness booleans. Same-origin
`POST /api/v1/auth/login` validates the password and sets `HttpOnly`, `SameSite=Lax`,
path `/`, and `Secure` on HTTPS; it never returns a session token in JSON. Cookie-authenticated
unsafe requests (`POST`, `PUT`, `PATCH`, `DELETE`) additionally require a same-origin
`Origin` plus the presence of `X-OBC-Auth`. `POST /api/v1/auth/logout` clears the cookie.

Extension bootstrap uses `POST /api/v1/auth/extension-token` only from an extension
origin. The request contains the one-time provisioned device key; the response contains a
bounded bearer session and `expires_at`. The server stores/configures only
`key-id:sha256-digest` records, never the complete device key. Extension access can be
disabled independently and extension sessions always have a finite TTL.

`POST /api/v1/auth/revoke` advances the monotonic `auth_state.session_epoch`, invalidating
all previously issued Web and extension sessions without rotating or exposing the signing
secret. Expired and revoked sessions return the same safe authentication errors. The
installer bearer is not a browser bootstrap secret and is not revoked by the session epoch.

```http
Authorization: Bearer <installer token or finite extension session>
```

Missing/malformed authentication returns `401`; a recognized but unauthorized credential
returns `403`. Errors use the shared `{ "error": { "code", "message" } }` envelope and never
echo a submitted password, header, cookie, device key, session, hash, or signing secret.
`GET /api/v1/system/readiness` is public. Onboarding is public only while
`onboarding_complete=false`; afterward it uses the same access policy and cookie-CSRF rules.

## Provisioning boundary

Browser authentication credentials are infrastructure secrets, not `UserSettings`:

| secret | private runtime representation |
|---|---|
| password | scrypt hash in `OPENBILICLAW_WEB_PASSWORD_HASH`; never plaintext |
| session signing key | random `OPENBILICLAW_SESSION_SECRET` |
| extension device key | complete key delivered once to the extension; only digest record retained in `OPENBILICLAW_EXTENSION_ACCESS_KEYS` |

Generate these through the installer/provisioning host and write them directly to the
mode-`0600` `.env` or a deployment secret store. Do not paste generated values into docs,
shell history, issue text, screenshots, API examples, logs, OpenAPI examples, or generated
clients. `GET/PATCH /api/v1/settings` exposes only `password_configured` and
`installer_bearer_configured` deployment facts plus mutable access behavior.

Task 22 is limited to generated-client and extension dispatcher wiring; these backend auth
contracts are already authoritative. This task does not claim a browser or native Windows
end-to-end run.
