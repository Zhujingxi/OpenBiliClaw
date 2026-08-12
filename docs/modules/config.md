# Configuration

`config.example.toml` is the authoritative current shape. Unknown sections and fields fail validation.

| Section | Fields |
|---|---|
| `[model]` | `provider`, `model_name`, optional opaque `credential_ref` |
| `[access]` | `method = "anonymous" | "manual"`, optional opaque `credential_ref` |
| `[content]` | `enabled` provider IDs |
| `[recommendation]` | `pool_target_count` (1..10000) |
| `[host]` | `api_host`, `api_port` |
| `[runtime]` | `default_timeout_seconds`, `default_resource_limit` |

Secrets are never valid inline values. Manual/model credentials are referenced through the credential vault. Environment variables use the `OPENBILICLAW_` names implemented in `core.config`; command-line values have highest precedence.

The target database defaults to `<data-dir>/openbiliclaw.db`. An unversioned existing application database stops startup and requires an explicit reset/import decision. Destructive target migrations require a verified backup.
