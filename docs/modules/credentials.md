# Credential Infrastructure

## Current landed boundary

`openbiliclaw.infrastructure.credentials.CredentialVault` stores secret bytes behind opaque
`cred_<32 hex>` references. It never has a read method that returns secret material: trusted provider
adapters call `resolve(reference, callback)`, receive a read-only memory view for that callback only,
and the temporary mutable buffer is zeroed on exit. `resolve_async()` keeps the same scoped view alive
only until a trusted async callback finishes (including cancellation), then zeroes it. Vault errors,
`repr`, telemetry, and tests do not include secret values.

The backend selection is explicit:

- `KeyringBackend` delegates to the installed OS `keyring` command without placing secret values in
  command arguments.
- `ProtectedFileBackend` is the fallback when the command is unavailable. On POSIX it requires an
  owner-only parent (`0700`) and credential file (`0600`), writes through fsync + atomic replace, and
  refuses a file whose group/other permission bits become readable.

The fallback protects local access permissions; it is not presented as encrypted storage. Operators
should prefer a working OS keyring, protect the account and backup media, and never copy the fallback
into logs or general migration archives without an explicit secret-transfer decision.

## Provider Access usage and composition status

Target `openbiliclaw.access.manual.ManualAccessMethod` now uses this vault directly: validated manual
form submissions are stored immediately, opaque references alone enter `CredentialAccessHandle`,
replacement preserves the reference with a bumped revision, and disconnect revokes it. Provider-owned
`CredentialVerifier` callbacks are the only website-credential readers. AST tests prohibit model-visible
`ai/` and future `assistant/` code from importing the credential package.

This path is still not wired into the production composition root. Existing credentials retain their
documented legacy behavior until Plan 13 host cutover and Plan 15 Runtime Composition. Future
model-visible, host, and frontend modules must not import this package; only trusted provider/access
adapters may resolve a reference.
