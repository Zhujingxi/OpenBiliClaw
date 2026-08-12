# Unified PydanticAI Providers

`src/openbiliclaw/ai/providers/` is the single model-construction boundary. “Unified” means one strict configuration shape, one `ModelFactory`, PydanticAI native providers, and one `AIRuntime.run()` execution path. It does not mean forcing every vendor through an OpenAI-compatible proxy.

## Chat model construction

`ModelInstanceConfig` is frozen and rejects unknown fields. It contains provider kind (`openai`, `anthropic`, `google`, or `openrouter`), model name, optional endpoint override, opaque CredentialVault secret reference, reviewed options, declared capabilities, owner, and provider version.

`ModelFactory` selects only thin native PydanticAI constructors:

- `OpenAIProvider` + `OpenAIChatModel`;
- `AnthropicProvider` + `AnthropicModel`;
- `GoogleProvider` + `GoogleModel`;
- `OpenRouterProvider` + `OpenAIChatModel`.

These modules contain no request logic. Credentials resolve only inside the selected trusted constructor callback. OpenRouter’s native provider does not accept a base URL, so an endpoint override fails explicitly rather than being ignored.

`BuiltModel` preserves a stable non-secret fingerprint and the declared-versus-verified capability distinction. Structured output, native tools, vision, and streaming remain unverified until explicit probes succeed. Production calls flow from Composition’s configured model through `RouteTable` and `AIRuntime.run()`; no application-owned parallel chat integration remains.

## Embeddings

Embedding configuration uses the same provider/model/endpoint/secret-reference shape as chat. It does not introduce a separately configured HTTP endpoint or custom JSON transport. `NativeEmbeddingTransport` uses the embeddings resource on the client owned by PydanticAI’s configured `OpenAIProvider`.

PydanticAI’s Anthropic, Google, and OpenRouter provider abstractions do not currently expose a common native embeddings resource in this dependency version, so those combinations fail closed with `UnsupportedCapabilityError`. The existing embedding service still owns deterministic batching, resource budgets, retry/timeout/cancellation, vector count/dimension validation, usage attribution, and provenance.

Note: no production consumer constructs the embedding service from composition yet; the contract and transports are landed and tested, wiring lands with the first semantic-search consumer.

## Operational boundary

OpenBiliClaw never downloads, bundles, starts, supervises, or serves model runtimes. Providers connect to externally served models. Future local inference must run as a separate service and be reached through one of the supported native provider APIs.

## Dependencies

The application pins `pydantic-ai-slim[anthropic,google,openai,openrouter]`. Vendor SDKs are dependency details of the native PydanticAI provider layer, not independent OpenBiliClaw integrations.
