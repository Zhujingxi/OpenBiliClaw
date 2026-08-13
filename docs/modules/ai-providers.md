# Unified PydanticAI Providers

`src/openbiliclaw/ai/providers/` is the single model-construction boundary. “Unified” means one strict configuration shape, one `ModelFactory`, PydanticAI native providers, and one `AIRuntime.run()` execution path. It does not mean forcing every vendor through an OpenAI-compatible proxy.

## Chat model construction

`ModelInstanceConfig` is frozen and rejects unknown fields. It contains provider kind (`openai`, `anthropic`, `google`, or `openrouter`), model name, optional endpoint override, opaque CredentialVault secret reference, reviewed options, declared capabilities, owner, and provider version.

`ModelFactory` selects only thin native PydanticAI constructors:

- `OpenAIProvider` + `OpenAIChatModel`;
- `AnthropicProvider` + `AnthropicModel`;
- `GoogleProvider` + `GoogleModel`;
- `OpenRouterProvider` + `OpenAIChatModel`.

These modules contain no request logic. Credentials resolve only inside the selected trusted constructor callback. OpenRouter’s native provider does not accept a base URL, so an endpoint override fails explicitly rather than being ignored. The reviewed `disable_thinking` option affects only the OpenAI constructor: it sets PydanticAI `extra_body` to `{"thinking": {"type": "disabled"}}`; when false, no `extra_body` is added. This supports thinking-always-on OpenAI-compatible endpoints whose forced output tools require `tool_choice = "required"`, without exposing a generic request-body escape hatch.

`BuiltModel` preserves a stable non-secret fingerprint and the declared-versus-verified capability distinction. Native tool-call output, native tools, vision, and streaming remain unverified; opt-in probe primitives and an in-memory store exist, but Composition does not run or persist probe results. Understanding achieves validated structured output with PydanticAI `PromptedOutput`: providers return schema-guided JSON as ordinary text, then Pydantic validates a model-friendly draft and the application attaches deterministic IDs/timestamps. Production calls flow from Composition’s configured model through `RouteTable` and `AIRuntime.run()`; no application-owned parallel chat integration remains.

### Verified OpenAI-compatible tool matrix

| Provider / endpoint | Model | `tool_choice = "required"` | `disable_thinking` |
| --- | --- | --- | --- |
| Kimi coding / `https://api.kimi.com/coding/v1` | `kimi-for-coding` | Yes, when thinking is disabled | Required |
| DeepSeek / `https://api.deepseek.com` | `deepseek-chat` | Yes, natively | Not needed (default path) |
| Other endpoints | Unverified | Unverified | Off by default |

OpenAI-compatible endpoints are not assumed to share tool/thinking compatibility. The toggle targets endpoints that force-enable thinking, specifically the verified Kimi coding plan whose thinking mode rejects forced `tool_choice = "required"`. It is not a general model workaround: false/absent sends the standard OpenAI request unchanged, non-OpenAI constructors ignore it, and the DeepSeek profile verifies the untouched default path.

## Embeddings

Embedding configuration uses the same provider/model/endpoint/secret-reference shape as chat. It does not introduce a separately configured HTTP endpoint or custom JSON transport. `NativeEmbeddingTransport` uses the embeddings resource on the client owned by PydanticAI’s configured `OpenAIProvider`. Official OpenAI requests include the configured `dimensions`; custom endpoints omit that vendor-specific request parameter, while `EmbeddingService` still validates every returned vector against `output_dimensions`.

PydanticAI’s Anthropic, Google, and OpenRouter provider abstractions do not currently expose a common native embeddings resource in this dependency version, so those combinations fail closed with `UnsupportedCapabilityError`. The existing embedding service still owns deterministic batching, resource budgets, retry/timeout/cancellation, vector count/dimension validation, usage attribution, and provenance.

Production Composition constructs `EmbeddingService` from `[embedding]` and exposes it as an inspectable `ApplicationServices` boundary. For `BAAI/bge-small-zh-v1.5`, `embed_query()` prepends the model-card Chinese retrieval instruction (`为这个句子生成表示以用于检索相关文章：`); document embeddings are never prefixed. No durable semantic index consumes it yet: L4 confirmed Recommendation discovery is still text-query based and therefore deliberately added no speculative table. Ownership and ingestion design will land only with a concrete semantic retrieval consumer.

## Operational boundary

OpenBiliClaw never downloads, bundles, starts, supervises, or serves model runtimes. Providers connect to externally served models. Future local inference must run as a separate service and be reached through one of the supported native provider APIs.

## Dependencies

The application pins `pydantic-ai-slim[anthropic,google,openai,openrouter]`. Vendor SDKs are dependency details of the native PydanticAI provider layer, not independent OpenBiliClaw integrations.
