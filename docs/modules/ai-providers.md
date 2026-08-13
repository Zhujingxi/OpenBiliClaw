# Catalog-driven PydanticAI Providers

`src/openbiliclaw/ai/providers/` is the single model-construction boundary. Chat provider metadata is not maintained in this repository: `ModelCatalog` reads `https://models.dev/api.json`, validates the relevant provider/model fields, and caches the response as `models.dev.json` in the configured data directory.

## Catalog loading and offline behavior

A cache younger than 24 hours is used without a request. On an absent or stale cache the service fetches models.dev and atomically replaces the cache after validation. If refresh fails, a valid stale cache remains usable. If no valid cache exists, construction raises a typed `CatalogError` naming the cache path. The normal test suite uses `tests/fixtures/models.dev.small.json`; it never accesses the network.

## Resolution and dispatch

`[model].provider` is a models.dev provider ID and `model_name` must exist under that provider. The catalog supplies the endpoint and capabilities (`tool_call`, `structured_output`, `attachment`, reasoning, and context limit). An explicit capability table replaces the complete catalog capability set; partial overrides are rejected.

| models.dev `npm` marker | Protocol family | PydanticAI construction |
| --- | --- | --- |
| `@ai-sdk/openai`, `@ai-sdk/openai-compatible` | OpenAI | Native `OpenAIProvider`/`DeepSeekProvider` registry matches; generic `OpenAIProvider` for all other IDs |
| `@ai-sdk/anthropic` | Anthropic | `AnthropicProvider` |
| `@ai-sdk/google` | Google | `GoogleProvider` |
| `@openrouter/ai-sdk-provider` | OpenRouter | `OpenRouterProvider` |

Unknown markers fail with `UnsupportedProtocolError` containing the marker. Within the OpenAI family, PydanticAI's registry resolves `openai` and `deepseek` to their native constructors, preserving DeepSeek's vendor reasoning profile without an OpenBiliClaw provider-ID switch. Other registry classes—including Alibaba, Hugging Face, Moonshot AI, Nebius, and OVHcloud in the current dependency—fall back to generic `OpenAIProvider` with the catalog endpoint; OpenBiliClaw does not assume that every registry provider exposes the same constructor contract.

A provider ID absent from models.dev is accepted only when `[model]` declares `protocol`, `endpoint`, and every field in `[model.capabilities]`. This fully explicit path is the escape hatch for private services or catalog errors; it does not add provider knowledge to the application.

`ModelInstanceConfig` contains the resolved free-form provider ID, protocol, model, endpoint, opaque vault reference, options, and capabilities. `ModelFactory` resolves credentials only inside the selected constructor. `BuiltModel` preserves a non-secret fingerprint and declared-versus-verified capabilities. Production calls still use `RouteTable` and `AIRuntime.run()`.

## Kimi coding endpoint

models.dev identifies `kimi-for-coding` as Anthropic protocol at `https://api.kimi.com/coding/v1`. Catalog resolution therefore uses `AnthropicProvider` and leaves thinking enabled; no endpoint or disable-thinking exception is configured. The narrow OpenAI `disable_thinking` option remains available only for fully explicit OpenAI-protocol deployments that require it.

## Embeddings

Embedding configuration remains explicit in this pass. `NativeEmbeddingTransport` uses the embeddings resource on PydanticAI's `OpenAIProvider`; unsupported protocol families fail closed. Official OpenAI requests include configured dimensions and custom endpoints omit that vendor-specific parameter while response vectors are still validated.

## Operational boundary

OpenBiliClaw never downloads, bundles, starts, supervises, or serves model runtimes. Catalog data contains no credentials. Secrets remain opaque vault references and are resolved only by trusted constructors.
