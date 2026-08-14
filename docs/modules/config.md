# Configuration

`config.example.toml` is the authoritative shape. Unknown sections and fields fail validation.

| Section | Fields |
|---|---|
| `[model]` | models.dev `provider`, `model_name`, opaque `secret_ref`; optional endpoint override; custom-only `protocol` |
| `[model.capabilities]` | complete override: `tools`, `structured_output`, `vision`, `context_tokens`, `streaming`, `reasoning` |
| `[model.options]` | `disable_thinking` (default `false`) |
| `[embedding]` | explicit provider/model/endpoint/secret fields plus `output_dimensions` |
| `[content]` | enabled provider IDs |
| `[recommendation]` | `pool_target_count` (1..10000) |
| `[host]` | `api_host`, `api_port`, optional opaque `bearer_secret_ref` |
| `[runtime]` | `default_timeout_seconds`, `default_resource_limit`, optional `[runtime.agents."<agent-id>"]` per-agent run-budget overrides (unset fields keep code defaults) |

## Catalog model

For a catalog model only the provider ID, model name, and secret reference are required:

```toml
[model]
provider = "deepseek"
model_name = "deepseek-chat"
secret_ref = "vault:cred_..."
```

The endpoint, protocol, and capabilities come from models.dev. Provider IDs are free-form strings, so existing `openai`, `anthropic`, `deepseek`, `google`, and `openrouter` configurations remain syntactically valid. The configured model must exist in the current catalog provider entry.

## Fully custom provider

A provider absent from models.dev must declare the complete escape-hatch shape:

```toml
[model]
provider = "private-gateway"
model_name = "private-model"
protocol = "openai"
endpoint = "https://gateway.example/v1"
secret_ref = "vault:cred_..."

[model.capabilities]
tools = true
structured_output = true
vision = false
context_tokens = 32768
streaming = true
reasoning = false
```

Supported explicit protocols are `openai`, `anthropic`, `google`, and `openrouter`. Omitting any of protocol, endpoint, or the complete capability table fails closed. Catalog capability overrides also require the complete table so catalog truth is never accidentally mixed with a partial declaration.

The catalog cache is `<data-dir>/models.dev.json`, fresh for 24 hours. Refresh failure uses a valid stale cache; first use while offline raises a clear typed error. No catalog snapshot is shipped in production.

`model.options.disable_thinking` affects only OpenAI-protocol construction and is off by default. Catalog-routed `kimi-for-coding` uses its declared Anthropic protocol and does not require the option.

The Web Settings Model section is the normal configuration path. Its API key field is write-only: a non-empty value creates a new vault credential and stores only its opaque reference, while an empty field keeps the existing reference. The API atomically rewrites only the model-owned TOML tables and preserves other sections/comments; comments inside the replaced model tables are not retained. The response currently requires a process restart to apply the saved graph.

Secrets are never valid inline values. Model, host bearer, and content credentials are referenced through the credential vault. Environment variables use the `OPENBILICLAW_` names implemented in `core.config`; CLI values have highest precedence.
