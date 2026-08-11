# Module Plan 04: Model and Embedding Providers

## Outcome

Construct PydanticAI models and typed embedding clients under `src/openbiliclaw/ai/providers/` without proxying every request through another abstraction. Validate actual provider capabilities before any agent route depends on them.

## Target package

```text
ai/providers/
├── models/
│   ├── factory.py
│   ├── openai.py
│   ├── anthropic.py
│   ├── google.py
│   ├── ollama.py
│   └── openrouter.py
├── embeddings/
│   ├── protocol.py
│   ├── service.py
│   └── providers/
└── diagnostics.py
```

Provider files contain construction and provider-specific diagnostics only. They do not contain prompts, routing policy, retries owned by workflows, or domain behavior.

## Typed contracts

- `ModelInstanceConfig`: provider kind, model name, endpoint, secret reference, and declared options.
- `BuiltModel`: PydanticAI `Model`, stable instance ID, verified capability record, and ownership metadata.
- `EmbeddingProvider`: typed `embed_documents()` and `embed_query()` methods returning dimension-checked vectors and usage.
- `EmbeddingModelInfo`: provider, model, dimensions, normalization, and version used for provenance/cache keys.
- `ProviderDiagnostic`: safe health result with no credential or response-body leakage.

## Internal phases

### Phase 1 — Provider inventory and dependency cleanup

- Map current OpenAI, Anthropic, Gemini, Ollama, OpenRouter, DashScope, and Codex-specific behavior to PydanticAI-native support or a minimal construction adapter.
- Remove direct SDK dependencies that PydanticAI makes redundant after verifying required features.
- Keep direct SDKs only where embeddings or provider diagnostics require them.
- Pin compatible versions and document optional extras deliberately.

### Phase 2 — Model factories

- Implement one explicit factory branch per supported provider kind.
- Resolve model-provider credentials only while constructing trusted provider clients.
- Normalize endpoint/proxy configuration without rewriting provider request payloads.
- Reject unknown provider options rather than forwarding arbitrary dictionaries.
- Build health diagnostics independently from domain agent runs.

### Phase 3 — Capability verification

- Define small opt-in contract probes for structured output, native tools, vision, streaming, and context behavior.
- Store verified capability results keyed by provider/model/version/config fingerprint.
- Treat configuration declarations as claims until verified; production routes requiring an unverified capability fail startup or remain disabled.
- Provide an explicit unsupported result for local models that cannot honor an agent contract; never emulate native tools by prompt parsing.

### Phase 4 — Embedding service

- Keep embeddings separate from PydanticAI chat models.
- Implement dimension validation, deterministic batching, bounded concurrency, retry classification, usage attribution, and model-version provenance.
- Key caches by content digest plus complete embedding model identity.
- Reject mixed vector dimensions at repository boundaries.
- Preserve only provider implementations needed by configured product behavior.

### Phase 5 — Configuration and diagnostics

- Replace legacy provider-chain and caller-prefix configuration with typed model instances and explicit agent routes.
- Expose safe CLI/API diagnostics for provider availability and verified capabilities.
- Never expose secrets, authorization headers, raw provider bodies, or full user prompts.
- Delete legacy provider modules after AI Runtime and all domain agents stop importing them.

## Tests and quality gates

- Unit tests mock provider constructors and embedding transports at the network boundary.
- Contract tests assert the shape of every supported configuration.
- Mark real capability probes as integration tests requiring explicit credentials.
- Add vector dimension, empty-input, batch-boundary, timeout, and cancellation tests.
- MyPy must type every SDK boundary; local stubs or narrow typed adapters are preferred to `Any` suppression.

## Documentation updates during implementation

Update AI/model configuration docs, optional dependency/install docs, model capability tables, diagnostics docs, and `docs/changelog.md`.

## Completion criteria

- Each configured model instance produces a PydanticAI model and verified capability record.
- Embeddings have independent typed configuration and provenance.
- No provider adapter reimplements generic completion, tool loops, or domain prompts.
- Unsupported local/provider models fail clearly instead of receiving degraded fake-tool prompts.
