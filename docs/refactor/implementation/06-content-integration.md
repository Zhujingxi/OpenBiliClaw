# Module Plan 06: Content Integration

## Outcome

Create a thin typed integration layer under `src/openbiliclaw/content/integration/` that registers providers, exposes capabilities, routes actions, and supplies purpose-specific projections without flattening provider-native data or owning provider behavior.

## Target package

```text
content/integration/
├── identity.py        # ProviderId, ContentKind, ContentRef
├── native.py          # NativeContent base and provider schema identity
├── capabilities.py    # narrow capability protocols
├── projections.py     # ContentPreview, Candidate, SearchDocument, CardData
├── actions.py         # typed provider action requests/results
├── manifest.py        # provider metadata and supported contracts
├── registry.py        # duplicate-safe typed registry
├── tools.py           # native tool exposure from capabilities
└── errors.py
```

## Core type strategy

Provider packages use generic native models internally. At the heterogeneous registry boundary they return a typed `NativeContent` base model carrying provider ID, content kind, provider content ID, schema version, canonical URL, and safe native payload model. Cross-provider consumers normally use projections and never inspect untyped dictionaries.

No contract may contain `Any`. Unknown external JSON is validated into provider-owned Pydantic models before entering this layer.

## Capability contracts

Define separate narrow protocols rather than one provider god interface:

- `SearchCapability`
- `FeedCapability`
- `FetchCapability`
- `RelatedCapability`
- `CreatorCapability`
- `HistoryCapability`
- `SavedCapability`
- `ActionCapability[RequestT, ResultT]`
- `ProjectionCapability`
- optional `ObservationCapability`

A provider manifest advertises capabilities, but runtime registration also validates that the implementation satisfies them.

## Internal phases

### Phase 1 — Identity and schema rules

- Define stable provider/content identifiers as validated value objects, not loose strings.
- Define canonical content-reference equality and hashing.
- Require provider schema versions for persisted native records.
- Define URL normalization as provider-owned behavior; the integration layer stores the result.
- Test serialization and identity stability across process restarts.

### Phase 2 — Native base and projections

- Define the minimal `NativeContent` base model.
- Define `ContentPreview`, `RecommendationCandidate`, `SearchDocument`, and `CardData` independently so one projection does not become a universal schema.
- Keep recommendation-only fields out of cards and presentation-only fields out of search documents.
- Require provenance and source timestamps on projections.
- Add schema snapshot tests for cross-language/API-visible projections.

### Phase 3 — Capabilities and actions

- Define typed query, pagination, filter, action, and result values for each shared capability.
- Use opaque provider cursor strings wrapped in typed cursor objects; do not interpret them centrally.
- Separate read and mutation capabilities.
- Require explicit idempotency and confirmation metadata for mutations.
- Normalize only integration failures: unavailable capability, invalid content reference, access denied, rate limited, and provider unavailable.

### Phase 4 — Registry and discovery

- Register providers by stable ID with duplicate and capability validation.
- Expose immutable provider manifests and safe availability status.
- Avoid runtime import scanning. Composition imports first-party providers explicitly; external package entry points may be supported only through the typed extension registry.
- Do not allow providers to depend on or discover one another through the registry.

### Phase 5 — Tool exposure

- Generate PydanticAI native tools from provider-owned typed capability functions and schemas.
- Expose only providers/capabilities relevant to the current Assistant run.
- Enforce preview/detail result budgets and strip untrusted instructions from tool metadata.
- Mutation tools return pending-action descriptors and execute only after the application confirmation workflow.
- Deterministic product workflows continue calling capabilities directly.

### Phase 6 — Replace source protocol

- Replace `sources/protocol.py::SourceAdapter`, `SourceRecipe`, source registry, and fake `SourceToolDispatcher` with the new contracts.
- Do not add an adapter preserving old `SourceAdapter.fetch()` semantics.
- Move source-specific normalization into provider packages.
- Delete generic recipes or capability flags that no target workflow uses.

## Tests and quality gates

- Contract-test every registered provider manifest against its implementation.
- Test every projection's size, required provenance, and serialization.
- Test registry duplicate, missing capability, and schema-version failures.
- Tool tests inspect generated schemas and bounded results using PydanticAI test models.
- Architecture tests prevent Content Integration from importing concrete providers or product modules.

## Documentation updates during implementation

Create/update the Content Integration module doc, provider authoring contract, API schemas, architecture diagrams, and `docs/changelog.md`.

## Completion criteria

- Providers retain native typed models.
- Cross-provider workflows use explicit projections.
- Provider APIs are callable without an LLM.
- Agent tools wrap the same typed capability methods.
- Content Integration contains no provider-specific conditionals or recommendation policy.
