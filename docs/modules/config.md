# Configuration

`config.example.toml` is the authoritative current shape. Unknown sections, fields, and model provider kinds fail validation.

| Section | Fields |
|---|---|
| `[model]` | `provider`, `model_name`, optional native-provider `endpoint`, opaque `secret_ref` |
| `[model.options]` | `disable_thinking` (default `false`) |
| `[embedding]` | the same provider fields plus `output_dimensions` |
| `[content]` | `enabled` provider IDs |
| `[recommendation]` | `pool_target_count` (1..10000) |
| `[host]` | `api_host`, `api_port`, optional opaque `bearer_secret_ref` |
| `[runtime]` | `default_timeout_seconds`, `default_resource_limit` |

Supported model provider kinds are `openai`, `anthropic`, `deepseek`, `google`, and `openrouter`. Both AI sections use the same PydanticAI-native provider configuration. `model.options.disable_thinking` is a narrow OpenAI-constructor compatibility toggle: when enabled it sends `thinking = {type = "disabled"}` in the provider request body. Use it for thinking-always-on OpenAI-compatible endpoints such as `kimi-for-coding` when PydanticAI forces `tool_choice = "required"`; other native provider constructors ignore it. Native embedding access currently requires the OpenAI provider; unsupported providers fail closed. For official OpenAI embedding endpoints, `output_dimensions` is requested from the provider. For custom endpoints it is only the required response-vector dimension, because the OpenAI-specific request parameter is omitted. The application does not host models.

Secrets are never valid inline values. Model, host bearer, and content credentials are referenced through the credential vault. `OPENBILICLAW_API_BEARER_SECRET_REF` accepts only an opaque vault reference, never the bearer value. Non-loopback API binding fails closed unless `host.bearer_secret_ref` resolves successfully. Other environment variables use the `OPENBILICLAW_` names implemented in `core.config`; command-line values have highest precedence.

The database defaults to `<data-dir>/openbiliclaw.db`. An unversioned existing application database stops startup and requires an explicit reset/import decision. Destructive target migrations require a verified backup.
