# Module Plan 07: Content Providers

## Outcome

Move each external content service into a self-contained first-party package under `src/openbiliclaw/content/providers/`. Every provider owns native schemas, access semantics, typed capabilities, projections, tools, presentation descriptors, and optional observation import. Browser-session execution is not implemented; unsupported authenticated capabilities are reported explicitly.

## Per-provider package shape

```text
content/providers/<provider>/
├── manifest.py
├── models.py
├── client.py
├── auth.py
├── capabilities.py
├── projections.py
├── presentation.py
├── tools.py
└── tests/                    # contract fixtures may remain under repository tests
```

Files that are unnecessary for a simple provider must not be created. A public-feed provider may need only `manifest.py`, `models.py`, `client.py`, and `projections.py`.

## Provider implementation standard

- Validate all external payloads at the HTTP/CLI boundary into provider-native Pydantic models.
- Keep endpoint paths, signatures, pagination, and provider errors local.
- Receive an `AccessHandle`; resolve secrets only inside trusted request authorization code.
- Return native models or shared projections, never raw JSON dictionaries.
- Bound result counts and payload sizes.
- Use deterministic fixture-based tests for external schema drift.
- Avoid inheritance hierarchies across unrelated providers.

## Internal phases

### Phase 1 — Reference Bilibili provider

- Implement Bilibili first as the complete reference because it exercises public access, manual cookie access, native video/article models, search/feed/fetch/related/history/saved capabilities, actions, cards, and observations.
- Consolidate `bilibili/`, `sources/bilibili_adapter.py`, `sources/bili_tasks.py`, and Bilibili producer behavior into one package.
- Keep deterministic API/client behavior; remove browser-manager coupling from the target provider.
- Define explicit degradation for operations requiring unavailable session/browser execution.
- Pass the complete provider contract suite before extracting no generic base class beyond the already-defined contracts.

### Phase 2 — Public/API-oriented providers

- Implement providers that can operate anonymously or with stable manual API credentials, such as YouTube, Bangumi, V2EX, and provider capabilities that use supported public endpoints.
- Move retained `youtube/client.py` and `youtube/takeout.py` behavior into the YouTube provider package, then delete the old top-level `youtube/` package.
- Port only proven provider-specific behavior and fixtures.
- Remove source recipes and runtime producer wrappers; discovery calls provider capabilities directly.
- Verify pagination, canonical IDs, published timestamps, and media projection.

### Phase 3 — Manual-credential providers

- Implement providers whose useful capabilities require PAT/API token/manual cookies, such as Reddit, X, Zhihu, LinuxDo, or configured services.
- Declare exact connection forms and verification semantics through Provider Access.
- Narrow requested permissions by capability.
- Ensure unavailable mutation/session functions do not appear in manifests or agent toolsets.

### Phase 4 — Session-fragile providers

- Implement safe public/manual portions of RedNote and Douyin without browser automation, cookie extraction, or extension task dispatch.
- Model rotating/session-bound credential limitations as degraded capability status.
- Do not fake support by accepting credentials that cannot be replayed reliably.
- Preserve native schemas and presentation descriptors so a future `AccessMethod` can unlock capabilities without changing downstream modules.

### Phase 5 — Projections, tools, and presentation

- Supply all required cross-provider projections with provenance.
- Register native tools only for implemented capabilities.
- Provide generic `CardData` for every content kind and optional trusted card variants for first-party hosts.
- Add snapshot/contract tests shared by Python OpenAPI and TypeScript presentation types.

### Phase 6 — Provider cleanup

- Delete superseded files under `sources/`, `bilibili/`, `saved_sync/`, and platform-specific runtime producers after callers move.
- Delete extension task protocols that exist solely for browser-backed provider execution and are outside target scope; retain only extension behavior explicitly owned by the Presentation/Observation plans.
- Remove provider switches from API, configuration, auth, and recommendation code.
- Remove dead dependencies after an import and packaging audit.

## Tests and quality gates

For every provider:

- Manifest/capability contract test.
- Native payload validation fixtures for success, empty data, auth failure, rate limit, deletion/tombstone, and schema drift.
- Projection and card snapshots.
- Access-scope and redaction tests.
- Pagination/idempotency tests where applicable.
- Optional marked live smoke test; never part of default CI.

## Documentation updates during implementation

Update each provider module doc, source-auth docs, supported-capability tables, frontend card docs, architecture diagrams, configuration docs, and `docs/changelog.md`.

## Completion criteria

- Every enabled provider is self-contained and passes the same contract suite.
- No central provider switch exists outside explicit Composition imports.
- No provider claims a capability it cannot execute with available access methods.
- No raw provider payload crosses into cross-provider workflows.
- Old source adapters, task dispatchers, and producer wrappers are removed.
