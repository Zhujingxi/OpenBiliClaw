# vNext API access control

`/api/v1` supports three coexisting, secret-separated authentication mechanisms:

- the installer bearer from `OPENBILICLAW_ACCESS_TOKEN` for operational/API clients;
- an HttpOnly `obc_session` cookie for the same-origin Web UI after password login;
- a finite bearer session for the browser extension, exchanged from a device key.

`GET /api/v1/auth/status` is public and returns only readiness booleans. Same-origin
`POST /api/v1/auth/login` validates the password and sets `HttpOnly`, `SameSite=Lax`,
path `/`, and `Secure` on HTTPS; it never returns a session token in JSON. Cookie-authenticated
unsafe requests (`POST`, `PUT`, `PATCH`, `DELETE`) and the lease-mutating source-task
claim GET additionally require a same-origin
`Origin` plus the presence of `X-OBC-Auth`. `POST /api/v1/auth/logout` clears the cookie.

Extension bootstrap uses `POST /api/v1/auth/extension-token` only from an extension
origin. The request contains the one-time provisioned device key; the response contains a
bounded bearer session and `expires_at`. The server stores/configures only
`key-id:sha256-digest` records, never the complete device key. Extension access can be
disabled independently and extension sessions always have a finite TTL.

Extension origins never receive `trust_loopback` or CORS authorization bypass. Even from a
loopback peer they must exchange a valid device key, then send the returned bearer explicitly;
cookie/loopback trust is not an extension authentication path. The API CORS allowlist contains
only loopback HTTP origins, so an extension preflight is not treated as authorization and no
`Access-Control-Allow-Origin` header is granted to extension origins.

Login and device-key exchange have separate, bounded per-peer failure limiters. The default is
five failures in 15 minutes, followed by a 15-minute lockout with `429`, `Retry-After`, and the
shared safe error envelope. Successful authentication clears that peer/kind entry; expired
entries are pruned, and each limiter retains at most 2048 peer entries. Neither submitted
passwords nor device keys appear in the limiter key, response, or log.

`POST /api/v1/auth/revoke` advances the monotonic `auth_state.session_epoch`, invalidating
all previously issued Web and extension sessions without rotating or exposing the signing
secret. Expired and revoked sessions return the same safe authentication errors. The
installer bearer is not a browser bootstrap secret and is not revoked by the session epoch.
At startup, the environment password hash is converted to a non-secret keyed fingerprint. A
fresh install with no password leaves no fingerprint row; first enable records a fingerprint
without revocation, and an unchanged state does not advance the epoch. Rotation, removal, and
re-enable are credential-state transitions: rotation replaces the fingerprint, removal stores
the explicit `disabled` sentinel, and re-enable replaces that sentinel even when the same old
hash returns. Each transition advances the epoch in the same database transaction, so old
sessions cannot survive removal or revive after re-enable. Repeated disabled state is idempotent.
Reconciliation failure closes session mint/verification instead of accepting a stale epoch.

```http
Authorization: Bearer <installer token or finite extension session>
```

Missing/malformed authentication returns `401`; a recognized but unauthorized credential
returns `403`. Errors use the shared `{ "error": { "code", "message" } }` envelope and never
echo a submitted password, header, cookie, device key, session, hash, or signing secret.
`GET /api/v1/system/readiness` is public. Onboarding uses the same access policy and
cookie-CSRF rules whenever any installer/browser/extension credential is configured, including
the fresh first-run window. Only an explicitly unconfigured manual-recovery deployment may
reach incomplete onboarding without authentication; the supported installers always provision
credentials before starting the API.

## Provisioning boundary

Browser authentication credentials are infrastructure secrets, not `UserSettings`:

| secret | private runtime representation |
|---|---|
| password | scrypt hash in `OPENBILICLAW_WEB_PASSWORD_HASH`; never plaintext |
| session signing key | installer-generated random `OPENBILICLAW_SESSION_SECRET`, persisted once and reused |
| extension device key | complete key delivered once to the extension; only digest record retained in `OPENBILICLAW_EXTENSION_ACCESS_KEYS` |

The source and Docker installers generate `OPENBILICLAW_SESSION_SECRET`, a Web password/hash,
and an extension key/digest before runtime or Compose preparation. They persist only the
signing secret, scrypt hash, and digest records in a private `.env` (POSIX mode `0600` or a
verified current-user-only Windows DACL), preserving non-empty
values on rerun. The plaintext password and complete extension key appear in exactly one
purpose-built `BOOTSTRAP_STATUS first_run_access` event, then are discarded and cannot be
recovered on rerun. Transfer that event privately; do not paste it into docs, shell history,
issue text, screenshots, API examples, general logs, OpenAPI examples, or generated clients.
`GET/PATCH /api/v1/settings` exposes only `password_configured` and
`installer_bearer_configured` deployment facts plus safe mutable access behavior.
`web_password_enabled` is read-only and remains enabled, so a cookie-authenticated browser
cannot remove its own login path. Lost first-run credentials are recovered with installer
`--rotate-access`, which replaces both verifier records only after a successful runtime check.

The authoritative vNext auth loader is environment-only: `OPENBILICLAW_ACCESS_TOKEN`,
`OPENBILICLAW_WEB_PASSWORD_HASH`, `OPENBILICLAW_SESSION_SECRET`, and digest-only
`OPENBILICLAW_EXTENSION_ACCESS_KEYS`. It never falls back to legacy `config.toml` auth fields,
and it never accepts an environment variable containing a complete extension device key.

Web and extension generated clients now consume these authoritative contracts. Web uses
same-origin cookie + CSRF; extension origins only use device-key exchange and finite bearer.
Native Windows and live-browser end-to-end runs remain separate verification environments.
