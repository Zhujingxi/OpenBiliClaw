# OpenBiliClaw

本地优先、类型安全的跨平台内容发现与推荐应用。

```text
Vue web / recipe-driven extension → typed FastAPI /v1 → Application workflows
Assistant correction → scoped pending approval → EditProfile ─┤
Credentialed History/Saves + YouTube Takeout ────────────────┤
                                      ├→ Observations → Understanding
Content Providers ← Provider Access ←─└→ Search/Feeds → Semantic Recall → Shadow Brief → Seeded Allocation → Constrained Selection
                    SQLite Semantic Index ← Embeddings ←─┘
            Infrastructure (SQLite/archive) ← Core lifecycle/jobs ← Composition
```

## Quick start

```bash
pip install -e ".[dev]"
cp config.example.toml config.toml
openbiliclaw check --config config.toml
openbiliclaw serve --config config.toml
```

Open http://127.0.0.1:8420. Docker uses the same `openbiliclaw serve` entrypoint.

## Current features

- Explicit anonymous/manual/plugin-assisted Provider Access; generic recipe capture converges on the verifier/vault and reconnects after restart.
- Eleven first-party content provider packages behind validated capabilities.
- Immutable observations, canonical understanding, and one supervised proactive recommendation pipeline.
- Optional Assistant/model routing plus a durable semantic index and adjacent recall through one unified PydanticAI-native provider path; the app does not serve models.
- Shared Vue 3/TypeScript presentation across desktop, mobile, and extension.
- Atomic runtime reload and backup-required destructive database migrations.

See [current architecture](docs/architecture.md), [current specification](docs/spec.md), [configuration](docs/modules/config.md), and [changelog](docs/changelog.md).
