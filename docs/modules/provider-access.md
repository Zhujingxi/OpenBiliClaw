# Provider Access

## Current boundary

`src/openbiliclaw/access/` is the typed vertical slice for anonymous and manual-credential access in production composition:

- frozen `AccessRequest`, `AccessMethodDescriptor`, and provider/account/permission/method value types;
- discriminated `AnonymousAccessHandle | CredentialAccessHandle`; a handle contains only provider/account scope, permissions, and an opaque `cred_<32 hex>` reference;
- six-state `AccessStatus` and `VerificationResult`; failures allow only closed, sanitized reasons and never carry provider response bodies;
- the `AccessMethod` protocol, typed registry, and Core `AccessMethodRegistration` metadata;
- the broker selects in request order only from caller-supported, user-allowed, provider-supported methods with sufficient permissions;
- anonymous methods permit only `read_public`; live probes map rate limits, regional restrictions, and network unavailability to safe states and never invent account identity;
- provider-owned `ConnectionForm`, field shape/length validation, and `ManualProviderSpec`; the central Access module has no provider switch;
- after validating manual/plugin submissions, the service writes directly to a provider/account-scoped opaque slot in `CredentialVault` and retains only the opaque handle. Replace reuses the reference and increments its revision; disconnect deletes the vault secret;
- `AccessService` provides connect/status/replace/disconnect/rehydrate. Successful-verification caching is bounded by both a five-minute default TTL and provider expiry, and credential replacement always re-verifies. Composition startup idempotently restores and re-verifies the default single-account connections in the vault, so connections survive restart;
- telemetry records only operation/provider/outcome; exception types and states contain no submitted value.

`access/` depends only on Core extension metadata, Infrastructure CredentialVault/telemetry, and the standard library/Pydantic. An AST gate prohibits imports from product/host/provider modules and prohibits model-visible `ai/` or future `assistant/` code from importing the credential package.

## Public Python contract

```text
AccessMethodDescriptor
AccessRequest
AccessHandle = AnonymousAccessHandle | CredentialAccessHandle
VerificationResult
AccessStatus
ConnectionForm / FormField
AccessMethod / AccessMethodRegistry
AccessBroker
AnonymousAccessMethod
ManualAccessMethod / ManualProviderSpec / CredentialVerifier
AccessService.connect | status | replace | disconnect
```

A provider auth adapter contributes its own `ConnectionForm`, capabilities, and async `CredentialVerifier`. The verifier receives a temporary read-only `memoryview` only inside the vault `resolve_async()` callback; the buffer is zeroed immediately after completion or cancellation without blocking the event loop. Raw secret submissions are not Pydantic models and have no serialization API; ephemeral `ValidatedSubmission.__repr__` for secret fields is always redacted.

## State semantics

| State | Meaning |
|---|---|
| `disconnected` | No handle |
| `unverified` | Credential is invalid or has no trusted evidence of success yet |
| `connected` | Permissions are sufficient and evidence remains within TTL/expiry |
| `degraded` | Insufficient scope, rate limit, invalid provider response contract, or session-only capability outside the current scope |
| `expired` | Provider explicitly reported expiry or expiry time has passed |
| `unavailable` | Geo-blocked or network unavailable |

An anonymous handle cannot carry an account ID or private-read/write permission. If successful verification grants fewer permissions than requested, AccessService fails closed to `degraded/insufficient_scope`.

## Plugin-assisted access (landed)

Browser-held credentials use provider-declared data and converge on the existing verified manual method after capture:

- `ProviderManifest.access_recipe` declares normalized domains, typed cookie/local-storage/session-storage artifacts, an optional declared-domain HTTPS warmup URL, and the target access method ID. It is frozen data with forbidden extra fields and no executable payload. Bilibili is the only currently declared recipe because its `builtin.manual` cookie verifier is real end to end (`SESSDATA` + `bili_jct`).
- Authenticated `GET /v1/sources/{id}/access-recipe` returns that data or typed 404. Authenticated `POST /v1/sources/{id}/access-material` requires the exact artifact identities, compiles cookie artifacts generically, validates the target method's existing form, writes only through `CredentialVault`, and invokes the same `CredentialVerifier`; malformed/missing/extra artifacts never write the vault.
- The extension stores only loopback origin plus the `openbiliclaw ext-token` value. It discovers source IDs, ignores recipe 404s, requests recipe-derived optional host permissions, reads only declared browser artifacts via generic `chrome.cookies`/`chrome.scripting` primitives, and posts them with bearer + CSRF headers. It contains no provider IDs, cookie names, or signing logic.
- Browser-executed content fetch/in-page signing (for example Douyin X-Bogus), automatic refresh scheduling, and multi-account capture remain explicitly deferred.

## Composition and exclusions

Composition supplies the credential vault, provider-owned methods, and availability refresh; Application workflows are the only host-facing entrypoint. Deleted host auth helpers and direct config credential reads have no compatibility or double-write path.

Managed-browser, OAuth, browser-executed signing/fetch, refresh scheduling, and multi-account are not implemented AccessMethods. Durable rehydration intentionally covers the local app's default account only; adding multi-account requires a separately approved durable account index. Presentation code cannot introduce provider-specific credential payloads.
