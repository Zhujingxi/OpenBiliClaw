# Privacy Policy

OpenBiliClaw is local-first software. This document describes the current refactored product boundary.

## Local data

Durable recommendations, observations, profile state, Assistant conversations, workflow idempotency records, and runtime state are stored in the configured local SQLite data directory. OpenBiliClaw does not operate a developer-owned collection service for this data.

User data is never silently reset or discarded. An unversioned existing application database or destructive migration stops and requires an explicit backed-up reset/import decision.

## External content providers

Enabled content providers receive only the requests required for the capabilities declared by their registered manifest. Public capabilities use anonymous access where available. Credentialed capabilities resolve opaque credential references only inside trusted Provider Access/provider callbacks.

Provider-native responses are validated at the provider boundary and projected into bounded purpose-specific records. Credentials, response bodies, cookies, and raw HTML are excluded from telemetry and model-visible projections.

## Model services

OpenBiliClaw does not install, bundle, or serve models. If a user configures chat or embedding, the relevant bounded input is sent to that user's external model service through PydanticAI's native provider layer. The chosen provider's privacy and retention terms apply to those requests.

API keys and provider secrets are stored behind `CredentialVault`, preferably using the OS keyring. The protected-file fallback enforces local filesystem permissions but is not presented as encrypted storage. Configuration files contain opaque references, not secret values.

## Browser extension

The extension is a presentation client and generic credential grabber for the configured loopback OpenBiliClaw backend. It stores the backend URL and an opaque extension token in its own local storage. When the user chooses to connect a provider, it requests that provider origin explicitly, then reads only the Cookie or site-storage entries named by a code-shipped provider recipe. It sends those short-lived values only to the user-configured loopback backend with the extension token; they are not sent to OpenBiliClaw-operated or other third-party services.

The extension does not collect browsing history, observe arbitrary page contents or behavior, run remote provider code, or serve/download models. Its manifest grants the local backend hosts and browser primitives needed by the generic recipe flow; provider-site host access is optional and requires per-origin user approval.

## Logs and telemetry

Telemetry uses bounded structured events and mandatory redaction. Authorization values, credential references, API keys, passwords, cookies, provider response bodies, and model payloads must not be logged. The repository does not enable a developer-operated analytics upload path by default.

## Deletion and backups

Users control the configured data directory and credential store. Stop OpenBiliClaw before deleting or backing up the data directory. Credential deletion must use the supported disconnect/revoke flow so the vault entry is removed as well as its opaque reference.

Backups containing SQLite data or protected-file credentials are sensitive. Protect backup media and never publish local config, data directories, cookies, API keys, or credential-vault files.

## Contact

Report privacy or security issues through the repository's GitHub issue/security channels without including secrets or private user data.
