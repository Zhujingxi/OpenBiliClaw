# CLAUDE.md

This file defines the current repository contract for coding agents.

## Product and authority

OpenBiliClaw is a local-first, evidence-based discovery product for Bilibili, Xiaohongshu, Douyin, YouTube, X, Zhihu, and Reddit. The authoritative runtime is v0.4/vNext: `/api/v1`, a fresh SQLite application database, a separate Huey SQLite queue, and LiteLLM Proxy.

There is no API, CLI, or stored-data backward compatibility. Historical files are never modified or imported. Historical design details live in Git history and `docs/changelog.md`.

## Commands

```bash
uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run lint-imports
uv run pytest --cov=openbiliclaw
```

```bash
cd extension
npm run api:check
npm run typecheck
npm test
npm run build
npm run build:firefox
```

Public CLI:

```text
openbiliclaw serve
openbiliclaw worker
openbiliclaw doctor
openbiliclaw eval
openbiliclaw db migrate
openbiliclaw db backup <destination>
```

## Architecture

- `features/` owns domain models, policies, repository ports, and application use cases.
- `infrastructure/` owns SQLAlchemy/Alembic, Huey, PydanticAI/LiteLLM, encryption, and source transports.
- `api/` owns composition, middleware, thin feature routers, and SSE boundaries.
- `web/` and `extension/` are generated-client consumers of the same OpenAPI contract.

Dependency direction is enforced with import-linter. Domain and application code do not import HTTP, ORM, queue, or provider implementation concerns. The app factory constructs dependencies, installs middleware, registers routers, performs startup checks, and shuts resources down; workflows belong in use cases or jobs.

## AI and sources

Generative tasks use typed `TaskSpec`/`TaskRunner` with `obc-interactive` or `obc-analysis`. Embeddings use `obc-embedding`. LiteLLM owns provider credentials, routing, fallback, network retry, cooldown, rate limits, budgets, and cache. Provider SDKs do not appear in application features.

Seven source connectors are registered explicitly. Each package exposes a manifest, strict settings model, real capability set, and connector. Unsupported operations stay absent. Browser-assisted execution uses only generic `/api/v1/source-tasks` claim/complete. Connectors return normalized `ActivityEvent` or `ContentItem`; transport rows never escape the package.

## Persistence and settings

The default application database is `data/vnext/openbiliclaw.db`; Huey uses `data/vnext/huey.db`. Alembic owns schema changes. Raw SQL is limited to migrations and isolated performance/operational queries.

Mutable product settings live in the application database and are exposed through `/api/v1/settings` and the existing settings UI. Source credentials use installer-generated encryption. Provider credentials remain exclusively in LiteLLM Admin. Infrastructure secrets live in private environment configuration and must never be printed, committed, or included in examples.

## Documentation requirements

Every change that touches interfaces, module boundaries, data flow, settings, CLI, dependencies, installation, or external integrations must update the matching active documentation:

1. affected `docs/modules/*.md` implemented behavior and public API;
2. a short bullet under the current block in `docs/changelog.md`;
3. for architecture changes, `docs/architecture.md`, `docs/spec.md`, and both README diagrams;
4. for CLI/settings changes, `docs/modules/cli.md` and `docs/modules/config.md`;
5. for installer changes, `docs/installation.md`, `docs/agent-install.md`, `docs/docker-deployment.md`, and installer completion text;
6. for retained-journey changes, `docs/manual-e2e.md` and the relevant `docs/e2e/` runbook;
7. `docs/index.md` when the authoritative document set changes.

Do not add live-looking historical commands or endpoints to active documentation. Git history is the archive.

## Pre-merge checklist

- [ ] strict Ruff/MyPy/import contracts pass;
- [ ] maintained Python and extension tests pass;
- [ ] OpenAPI client generation is deterministic;
- [ ] docs match the implemented API, CLI, settings, installer, and source capability matrix;
- [ ] no secret, old database, generated runtime state, or real credential is staged.
