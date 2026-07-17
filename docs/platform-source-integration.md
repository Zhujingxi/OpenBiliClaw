# vNext platform source integration

This guide describes the only supported way to add or change a source. It does not define a dynamic plugin system.

## 当前权威合同

The contracts below are the current authority for all seven built-in sources.

## 1. Define the contract

Create `infrastructure.sources.<source_id>` with:

- a canonical `SourceId` already accepted by the product contract;
- a frozen strict Pydantic settings model;
- a `SourceManifest` containing real capabilities and exact operation schemas;
- a `SourceConnector` that returns normalized `ActivityEvent` or `ContentItem`;
- mocked transport tests and the shared connector contract suite.

Capabilities describe product behavior. Operations describe executable bootstrap/search/trending/feed/related/creator/community work. Do not advertise an operation without a working primary transport. If a browser fallback exists, declare it explicitly. Unsupported operations stay absent.

## 2. Keep transport inside the package

HTTP, CLI, SDK, cookie replay, and DOM records are infrastructure details. Convert them to normalized domain models before returning. Platform response types, field names, status codes, and platform-specific conditionals must not escape the source package.

Normalization must preserve:

- canonical source and stable external ID;
- original content URL, title, author, publication/interaction time when known;
- content/activity type and evidence metadata;
- deterministic identity and deduplication behavior;
- the public `limit`, including multi-scope bootstrap operations.

Missing timestamps must be represented explicitly; do not replace them with task completion time.

## 3. Settings and credentials

Only publish settings with a real runtime consumer. Global source enablement, weights, scheduling, and feed policy belong to `UserSettings`, not per-source settings. Source settings are strict and secret-free.

When a source has a backend credential consumer, credentials use the write-only account configuration endpoint and encrypted `source_accounts` persistence. Extension-only sources publish no credential schema and reject account configuration before persistence. Status, manifest, errors, disconnect responses, OpenAPI, and logs must never expose plaintext, ciphertext, credential field values, or credential-shaped examples. Disconnect is idempotent.

## 4. Browser-assisted execution

Browser tasks use only:

```text
GET  /api/v1/source-tasks/claim
POST /api/v1/source-tasks/{task_id}/complete
```

Use the typed discriminated request/result for the operation. The backend validates source, operation, lease token, request deadline, and result shape. The extension generic dispatcher validates the claim against its local executor capability, applies the deadline through `AbortSignal`, and cleans up timers, listeners, and temporary tabs.

Do not add platform-specific claim/result endpoints, custom dispatcher queues, or detached cleanup tasks. A transport-mode change may block new browser enqueue while allowing already persisted work to drain under its enqueue-time contract.

## 5. Composition

Register the connector explicitly in API and worker composition. Construction must be zero-I/O: do not read credentials, instantiate authenticated clients, or perform network calls until an operation executes. API and worker rebuild the registry from persisted settings at operation/job boundaries.

No entry-point scanning, runtime plugin loading, or source-specific conditional belongs in application use cases.

## 6. UI and generated contracts

The manifest drives source status, settings, credential forms, capabilities, and browser-operation schemas. Update OpenAPI and regenerate both Web and extension clients. Existing `/setup`, `/web`, `/m`, and popup surfaces should consume generic source data rather than hard-coded endpoint strings.

If a user-facing setting is meaningful, expose it through the current settings/source UI. Infrastructure secrets remain hidden.

## 7. Required tests

For every source:

1. shared connector contract tests for every advertised operation;
2. mocked transport success, empty, malformed, auth, timeout, and rate-limit behavior;
3. normalization identity, URL, timestamp, limit, and deduplication tests;
4. manifest/schema parity and secret/non-finite rejection tests;
5. zero-I/O composition smoke;
6. generic browser dispatcher translation, mismatch, deadline, failure, and completion tests when browser-assisted;
7. API/OpenAPI and generated-client determinism checks;
8. rendered Web/popup smoke for status, configure, bootstrap, and content cards.

Run real source E2E only with explicit authorization and report it separately from mocked coverage. The vNext connector contract is read/import/discovery only; tests must not like, follow, favorite, save, subscribe, or otherwise mutate a platform account.

## 8. Current capability matrix

| source | retained operations | transport |
|---|---|---|
| Bilibili | bootstrap, search, trending, related | direct, with declared browser search fallback |
| Xiaohongshu | bootstrap, search, creator | browser |
| Douyin | bootstrap; search, trending, feed | browser bootstrap; configured direct/browser discovery |
| YouTube | bootstrap, search, trending, creator | browser bootstrap; direct discovery |
| X | bootstrap, search, feed, creator | retained direct/CLI adapter |
| Zhihu | bootstrap, search, trending, feed, creator, related | browser |
| Reddit | bootstrap, search, trending, community, related | browser extension |

The authoritative machine-readable matrix is `GET /api/v1/sources`; this table must change whenever manifests change.
