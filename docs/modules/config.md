# Configuration

`config.example.toml` is the authoritative current shape. Unknown sections, fields, and model provider kinds fail validation.

| Section | Fields |
|---|---|
| `[model]` | `provider`, `model_name`, optional native-provider `endpoint`, opaque `secret_ref` |
| `[embedding]` | the same provider fields plus `output_dimensions` |
| `[content]` | `enabled` provider IDs |
| `[recommendation]` | `pool_target_count` (1..10000) |
| `[host]` | `api_host`, `api_port` |
| `[runtime]` | `default_timeout_seconds`, `default_resource_limit` |

Supported model provider kinds are `openai`, `anthropic`, `google`, and `openrouter`. Both AI sections use the same PydanticAI-native provider configuration. Native embedding access currently requires the OpenAI provider; unsupported providers fail closed. The application does not host models.

Secrets are never valid inline values. Model and content credentials are referenced through the credential vault. Environment variables use the `OPENBILICLAW_` names implemented in `core.config`; command-line values have highest precedence.

The database defaults to `<data-dir>/openbiliclaw.db`. An unversioned existing application database stops startup and requires an explicit reset/import decision. Destructive target migrations require a verified backup.
