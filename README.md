# OpenBiliClaw

本地优先、类型安全的跨平台内容发现与推荐应用。

```text
Vue web / extension → typed FastAPI /v1 → Application workflows
                                      ├→ Observations → Understanding
Content Providers ← Provider Access ←─└→ Discovery → Evaluation → Selection
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

- Explicit anonymous/manual-secret Provider Access; secrets remain in the credential vault.
- Eleven first-party content provider packages behind validated capabilities.
- Immutable observations, canonical understanding, and one supervised proactive recommendation pipeline.
- Optional Assistant/model routing and a composition-wired embedding service through one unified PydanticAI-native provider path; the app does not serve models.
- Shared Vue 3/TypeScript presentation across desktop, mobile, and extension.
- Atomic runtime reload and backup-required destructive database migrations.

See [current architecture](docs/architecture.md), [current specification](docs/spec.md), [configuration](docs/modules/config.md), and [changelog](docs/changelog.md).
