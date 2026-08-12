# Frequently Asked Questions

## Installation

### How do I run OpenBiliClaw from source?

Follow [Installation](agent-install.md). The supported commands are:

```bash
openbiliclaw check --config config.toml
openbiliclaw serve --config config.toml
```

Removed setup, init, start, daemon, and legacy API aliases are not supported.

### How do I run it with Docker?

Follow [Docker deployment](docker-deployment.md). The container runs the same `serve` entrypoint and persists `/app/runtime`.

### The browser extension cannot connect

Confirm that `GET http://127.0.0.1:8420/v1/runtime/health` succeeds, then check the backend URL and opaque device token stored by the extension. The reduced extension is a backend client only: it does not read website cookies, collect browsing behavior, or execute provider tasks.

## Models and credentials

### Does OpenBiliClaw install or serve a model?

No. Chat and embeddings use external model services through PydanticAI native providers. OpenBiliClaw does not bundle or manage a local model runtime. See [AI providers](modules/ai-providers.md).

### Where do API keys and provider credentials live?

Configuration stores opaque credential references. Secret bytes live behind `CredentialVault` and are resolved only inside trusted provider/access callbacks. Do not place secrets in `config.toml` or commit a local configuration file. See [credentials](modules/credentials.md).

### Is embedding required?

No. Configure `[embedding]` only when a product capability needs it. In the current composition no production consumer constructs the embedding service; unsupported provider/capability combinations fail closed.

## Data and upgrades

### Where is user data stored?

The target runtime stores durable state in SQLite under the configured data directory. Existing unversioned application databases and destructive migrations require an explicit backed-up reset/import decision; startup never silently discards user data.

### Does the extension upload website activity?

No. The current extension only stores its backend URL and opaque device token and renders typed backend data. Website login state, cookie extraction, page observation, provider task dispatch, and browser-session execution were removed. See [Privacy](privacy.md).

### Where is the current behavior documented?

Start with [Architecture](architecture.md), [Specification](spec.md), and the [module index](index.md). Historical plans and pre-cutover guides are intentionally not retained.
