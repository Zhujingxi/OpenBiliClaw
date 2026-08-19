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

Confirm that `GET http://127.0.0.1:8420/v1/runtime/health` succeeds, then check the backend URL and opaque extension token stored by the extension. Provider access also requires approving the recipe-declared site origin; the extension reads only the Cookie/site-storage names in that code-shipped recipe and submits them only to the configured loopback backend.

## Models and credentials

### Does OpenBiliClaw install or serve a model?

No. Chat and embeddings use external model services through PydanticAI native providers. OpenBiliClaw does not bundle or manage a local model runtime. See [AI providers](modules/ai-providers.md).

### Where do API keys and provider credentials live?

Configuration stores opaque credential references. Secret bytes live behind `CredentialVault` and are resolved only inside trusted provider/access callbacks. Do not place secrets in `config.toml` or commit a local configuration file. See [credentials](modules/credentials.md).

### Is embedding required?

No. Composition constructs the service only when `[embedding]` is configured. With it, evidence, claims, and candidates are embedded into the durable V10 index and the adjacent exploration arm recalls semantically near-but-not-core candidates; without it, discovery stays text-query based and the adjacent arm simply has no supply. Unsupported provider/capability combinations fail closed.

## Data and upgrades

### Where is user data stored?

The target runtime stores durable state in SQLite under the configured data directory. Existing unversioned application databases and destructive migrations require an explicit backed-up reset/import decision; startup never silently discards user data.

### Does the extension upload website activity?

It does not upload browsing history, arbitrary page content, or behavior. When the user starts plugin-assisted provider access and approves the declared origin, the extension does transmit only the Cookie/site-storage values named by the code-shipped recipe to the user's configured loopback backend with its opaque extension token. That material is verified and vaulted locally and is not sent to OpenBiliClaw-operated or other third-party services. See [Privacy](privacy.md).

### Where is the current behavior documented?

Start with [Architecture](architecture.md), [Specification](spec.md), and the [module index](index.md). Completed feature plans and superseded pre-cutover guides are removed after current behavior is captured in maintained docs; the E2E plan/log remain as clearly marked historical verification records.
