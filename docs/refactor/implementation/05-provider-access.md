# Module Plan 05: Provider Access

## Outcome

Implement anonymous and manually supplied provider access under `src/openbiliclaw/access/`. Providers receive opaque scoped handles; agents, tools, logs, and frontend reads never receive raw website credentials. Browser extension sessions and managed browsers remain extension points only.

## Target package

```text
access/
├── models.py          # method descriptors, requests, handles, status
├── methods.py         # AccessMethod protocol and registry
├── anonymous.py
├── manual.py
├── broker.py          # selects and opens an allowed method
├── verification.py    # typed verification result and evidence
├── forms.py           # declarative provider-specific connection forms
└── service.py         # connect/status/replace/disconnect use cases
```

## Public contracts

- `AccessMethodDescriptor`: stable method ID, label, supported provider IDs, required interaction, and capabilities.
- `ConnectionForm`: typed declarative fields with secret/non-secret classification and validation constraints.
- `AccessRequest`: provider/account, requested permissions, and supported method IDs.
- `AccessHandle`: sealed discriminated union containing only opaque references and scope metadata.
- `AccessMethod`: opens, verifies, refreshes when supported, and closes a handle.
- `AccessStatus`: disconnected, unverified, connected, degraded, expired, or unavailable with safe evidence.
- `VerificationResult`: strength, timestamp, account identity if safe, expiry, and sanitized failure.

Raw secret types remain private to trusted adapters and cannot satisfy Pydantic serialization used by API or agent dependencies.

## Internal phases

### Phase 1 — Access model and trust boundary

- Define provider, account, permission, method, and credential-reference value objects.
- Make handles immutable and provider/account scoped.
- Separate credential acquisition from provider authentication semantics.
- Define safe status/error types and mandatory redaction.
- Add tests proving handles and statuses cannot serialize secret material.

### Phase 2 — Anonymous access

- Implement the anonymous access method with explicit capabilities and provider support.
- Verify public endpoint availability without inventing an account identity.
- Represent provider rate limits and geo/network restrictions as degraded/unavailable status.
- Ensure providers cannot accidentally request write permissions from an anonymous handle.

### Phase 3 — Manual credential forms

- Replace hard-coded auth UI branches with provider-declared `ConnectionForm` descriptors.
- Accept API tokens, PATs, cookies, and provider-specific fields through secret transport fields.
- Validate shape before writing; store secret values directly in `CredentialVault` and retain only references.
- Support replace and disconnect as atomic operations.
- Never echo submitted values or reveal whether a partial secret matched.

### Phase 4 — Verification and lifecycle

- Let provider-owned auth adapters verify provider-specific semantics such as CSRF, account identity, expiry, and scopes.
- Keep verification orchestration and status persistence in Access.
- Cache successful verification only until a bounded expiry or credential replacement.
- Revoke/delete stored secrets on disconnect.
- Define degraded status for providers whose richer session mode is outside scope.

### Phase 5 — Broker and extension contract

- Select only methods both the provider and user configuration allow.
- Return explicit unavailable reasons when no method can satisfy requested permissions.
- Register `AccessMethod` implementations through the typed extension registry.
- Specify, but do not implement, future browser-extension, managed-browser, and OAuth methods.
- Do not add browser-specific enums, payloads, or dependencies to the built-in implementations.

### Phase 6 — Host cutover and legacy removal

- Replace `api/source_auth/` and `auth_core.py` with thin host routes calling Access services.
- Move provider-specific verification from central conditionals into provider auth adapters.
- Delete legacy credential compatibility and config-based secret reads.
- Remove extension-cookie requirements from providers that support public/manual modes; mark unsupported operations explicitly.

## Tests and quality gates

- Secret-canary tests cover vault writes, exceptions, logs, API responses, model contexts, tool outputs, and telemetry.
- Contract tests run every provider's form and verification adapter against malformed, missing, expired, and insufficient-scope credentials.
- Test method selection and permission narrowing exhaustively.
- Tests use opaque fake handles, never plaintext fixtures committed to the repository.
- MyPy must prevent passing raw secret values where `AccessHandle` is required.

## Documentation updates during implementation

Update `docs/modules/source-auth.md`, API/config/security docs, provider connection docs, architecture diagrams, and `docs/changelog.md`.

## Completion criteria

- Anonymous and manual access work through one typed service.
- Provider-specific forms and verification require no central provider switch.
- Raw website credentials are absent from configuration, normal persistence, logs, API reads, tools, and model messages.
- Session-only features report degraded/unavailable status honestly.
- Future access methods can register without changing providers or product workflows.
