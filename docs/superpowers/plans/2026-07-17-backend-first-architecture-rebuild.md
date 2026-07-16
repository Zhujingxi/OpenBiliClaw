# OpenBiliClaw Backend-First Architecture Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy monolithic backend with a feature-oriented vNext product while retaining the working web/extension journey and the seven source integrations.

**Architecture:** FastAPI feature routers call application use cases and ports. SQLAlchemy/Alembic, Huey, PydanticAI through LiteLLM, encryption, extension transport, and platform clients are infrastructure adapters. The vNext database and queue are fresh files; the old data directory remains untouched as a manual archive.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, PydanticAI, Pydantic Evals, LiteLLM Proxy, SQLAlchemy 2, Alembic, SQLite, Huey, PostgreSQL for LiteLLM, vanilla TypeScript/JavaScript, OpenAPI.

## Global Constraints

- Work in place from the current product; do not redesign the frontend or add React.
- Web and extension remain; desktop packaging is removed.
- Retain Bilibili, Xiaohongshu, Douyin, YouTube, X, Zhihu, and Reddit at their current discovery/bootstrap capability.
- Retain onboarding, authentication, activity ingestion, evidence profile, discovery feed, feedback, chat, local favorites, and watch-later.
- Remove custom LLM providers/routes/migrations/hot swapping, Soul/MBTI/JSON profiles, awareness/insight/speculation/probes, delight/proactive notifications, native saves, OpenClaw, self-update, optimizer/persona agents, compatibility APIs, and feature CLI commands.
- LiteLLM is mandatory and owns credentials, providers, routing, fallback, cooldown, HTTP retry, rate limits, budgets, and cache.
- Model aliases are exactly `obc-interactive`, `obc-analysis`, and `obc-embedding`.
- The OpenBiliClaw database remains SQLite; Huey uses a separate SQLite file; LiteLLM alone uses PostgreSQL.
- All user-facing OpenBiliClaw settings remain UI configurable; provider configuration is delegated to LiteLLM Admin.
- `/api/v1` is the only public API namespace; progress/chat use SSE and extension source work uses generic claim/complete.
- Strict MyPy, Ruff `C901` maximum complexity 12, import-linter contracts, and at least 80% coverage for new core modules are required.
- Tests must not call live providers; use PydanticAI test models and mocked source transports.
- Follow `AGENTS.md`, `CLAUDE.md` documentation requirements, and `docs/platform-source-integration.md` safety rules.

---

### Task 16: Frozen Domain Contracts and Characterization Tests

**Files:**
- Create: `src/openbiliclaw/features/{activity,profile,feed,library,chat,sources}/domain.py`
- Create: `tests/vnext/test_domain_contracts.py`
- Modify: `pyproject.toml`, `src/openbiliclaw/__init__.py`

**Interfaces:**
- Produces immutable Pydantic contracts `ActivityEvent`, `ProfileSignal`, `ProfileSnapshot`, `ProfileDelta`, `ContentItem`, `CandidateAssessment`, `FeedEntry`, `Interaction`, `CollectionItem`, `ChatTurn`, `SourceManifest`, and `SourceConnector`.
- Produces deterministic `apply_profile_delta()` and `feed_deficit()` policies.

- [ ] Write tests that serialize and round-trip every frozen contract; verify evidence is mandatory, user overrides have confidence 1.0 and cannot be silently removed, duplicate facets merge case-insensitively, assessment scores clamp to 0..1, and feed deficit replenishes only below the low watermark.
- [ ] Run `uv run --frozen pytest tests/vnext/test_domain_contracts.py -q` and record the expected missing-module or missing-symbol failures.
- [ ] Implement only the contracts and deterministic policies required by the tests. Domain modules must not import FastAPI, SQLAlchemy, Huey, PydanticAI, or legacy Soul/storage code.
- [ ] Run the focused test and `uv run --frozen ruff check src/openbiliclaw/features tests/vnext/test_domain_contracts.py`.
- [ ] Commit with `feat: define vnext domain contracts`.

### Task 17: SQLAlchemy Persistence, Alembic Baseline, Settings, and Credentials

**Files:**
- Create: `src/openbiliclaw/infrastructure/database/{base,models,repositories,uow}.py`
- Create: `src/openbiliclaw/infrastructure/security/credentials.py`
- Create: `src/openbiliclaw/features/system/{domain,service}.py`
- Create: `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_vnext_baseline.py`
- Create: `tests/vnext/test_persistence.py`, `tests/vnext/test_settings.py`

**Interfaces:**
- Produces `DatabaseSettings`, `create_engine_and_session()`, `UnitOfWork`, feature repository protocols/adapters, `SettingsService`, and `CredentialCipher`.
- Creates tables: settings, source accounts, activity events, profile revisions/evidence, content items, candidate assessments, feed entries, interactions, collections/items, chat turns, source tasks, job runs, and AI runs.

- [ ] Write failing tests for a fresh SQLite schema, transaction rollback, unique content identity `(source_id, external_id)`, optimistic profile revision conflict, two predefined collections, typed setting validation, and credential encryption that never stores plaintext.
- [ ] Run the two focused test files and capture RED output.
- [ ] Implement SQLAlchemy 2 models/repositories/UoW, Alembic baseline, database-backed settings, and Fernet credential encryption derived from installer-generated `OPENBILICLAW_SECRET_KEY`.
- [ ] Verify migration upgrade/downgrade/upgrade against a temporary database and run focused tests, Ruff, and strict MyPy on the new modules.
- [ ] Commit with `feat: add vnext persistence and settings`.

### Task 18: Typed AI Boundary, Embeddings, Evals, and LiteLLM Stack

**Files:**
- Create: `src/openbiliclaw/infrastructure/ai/{spec,runner,embedding,health,tasks}.py`
- Create: `evals/datasets/{profile_delta,keyword_generation,candidate_assessment,recommendation_explanation}.yaml`
- Create: `litellm/config.yaml`
- Modify: `docker-compose.yml`, `docker-compose.prebuilt.yml`, `Dockerfile`, `pyproject.toml`, `uv.lock`
- Create: `tests/vnext/test_task_runner.py`, `tests/vnext/test_embedding_service.py`, `tests/vnext/test_ai_health.py`

**Interfaces:**
- Produces generic `TaskSpec[InputT, OutputT]`, `TaskRunner.run()`, `EmbeddingService.embed()`, and alias health results.
- `TaskSpec` carries task name, input/output types, reusable PydanticAI agent, model alias, semantic retry limit, timeout, usage limits, cache policy, and lane.

- [ ] Write failing tests using PydanticAI test models for input/output validation, semantic retry bounds, timeout, usage-limit failure, AI-run recording, and stable alias selection; mock OpenAI-compatible embedding/health HTTP responses.
- [ ] Run focused tests and capture RED output.
- [ ] Implement the boundary so no feature imports provider SDKs and the runner does not implement provider routing, HTTP retry, JSON repair, or fallback.
- [ ] Add Compose services `api`, `worker`, `litellm`, and `litellm-postgres`; configure `/ui`, health checks, persistent volumes, and the three exact aliases.
- [ ] Replace direct provider dependencies with PydanticAI/Pydantic Evals; update the lock; run focused tests and configuration validation.
- [ ] Commit with `feat: route typed ai tasks through litellm`.

### Task 19: Seven Capability-Based Source Connectors and Generic Source Tasks

**Files:**
- Create: `src/openbiliclaw/features/sources/{domain,registry,service}.py`
- Create: `src/openbiliclaw/infrastructure/sources/{bilibili,xiaohongshu,douyin,youtube,twitter,zhihu,reddit}.py`
- Create: `src/openbiliclaw/infrastructure/sources/browser_tasks.py`
- Create: `tests/vnext/sources/test_connector_contract.py` and one transport test per source

**Interfaces:**
- Produces `SourceManifest`, source-specific Pydantic settings, `SourceConnector`, explicit `build_source_registry()`, and `SourceTaskService.claim()/complete()`.
- Connectors return only `ActivityEvent` or `ContentItem`; SDK/CLI/DOM payloads do not escape source packages.

- [ ] Write a parametrized failing contract suite for all seven source IDs and their retained capability sets, normalization, stable identities, unsupported-operation rejection, and no account mutation.
- [ ] Write failing browser-task tests for typed operation/source IDs, lease-safe claim, idempotent completion, malformed payload rejection, and credential redaction.
- [ ] Implement explicit adapters around retained platform transports; do not add dynamic plugins or emulate unsupported capabilities.
- [ ] Run all connector/transport tests plus Ruff, MyPy, and import-linter.
- [ ] Commit with `feat: add capability based source connectors`.

### Task 20: Evidence Profile, Feed, Library, Chat, and Four Huey Jobs

**Files:**
- Create: `src/openbiliclaw/features/{activity,profile,feed,library,chat}/service.py`
- Create: `src/openbiliclaw/infrastructure/jobs/{queue,tasks}.py`
- Create: `tests/vnext/test_use_cases.py`, `tests/vnext/test_jobs.py`

**Interfaces:**
- Produces use cases for event ingestion, profile projection, feed replenishment/interaction, collection mutation, chat, and job inspection.
- Produces idempotent jobs `source_sync`, `profile_projection`, `feed_replenishment`, and `cleanup` with priorities interactive/user-triggered/scheduled-maintenance.

- [ ] Write failing tests for ActivityEvent to ProfileSignal projection, atomic ProfileDelta application, deterministic deficit/allocation/dedup/diversity admission, feedback affecting later rank, local-only collections, persisted chat turns, and SSE-compatible chat chunks.
- [ ] Write failing worker tests for priority, idempotency, retry, cancellation, duplicate scheduling, restart recovery, locks, and application DB job status as source of truth.
- [ ] Implement use cases behind repository/AI/source ports and Huey backed by its own SQLite file; do not use naked `asyncio.create_task()`.
- [ ] Run focused tests, Ruff, MyPy, and architecture contracts.
- [ ] Commit with `feat: implement vnext use cases and jobs`.

### Task 21: Thin FastAPI v1 Composition and Operational CLI

**Files:**
- Replace: `src/openbiliclaw/api/app.py`, `src/openbiliclaw/cli.py`
- Create: `src/openbiliclaw/api/dependencies.py`, `src/openbiliclaw/api/routers/*.py`
- Create: `src/openbiliclaw/worker.py`
- Create: `tests/vnext/test_api_v1.py`, `tests/vnext/test_cli.py`, `tests/vnext/test_openapi.py`

**Interfaces:**
- Produces only `/api/v1/system`, `/settings`, `/onboarding`, `/sources`, `/source-tasks`, `/events`, `/profile`, `/feed`, `/interactions`, `/library`, `/chat`, and `/jobs` router groups.
- Produces CLI commands `serve`, `worker`, `doctor`, `eval`, `db migrate`, and `db backup`.

- [ ] Write failing API tests for auth, validation, error mapping, OpenAPI operation IDs, progress/chat SSE, generic source claim/complete, and absence of legacy endpoints.
- [ ] Write failing CLI tests proving only the operational commands are exposed and the historical Typer private `_click` import is gone.
- [ ] Implement a small app factory limited to dependency construction, middleware, routers, startup checks, shutdown, and existing static web mounting.
- [ ] Generate `openapi/openapi.json`; run API/CLI/OpenAPI tests, Ruff, MyPy, import-linter, and a complexity check for the app factory.
- [ ] Commit with `refactor: cut over to vnext api and cli`.

### Task 22: Existing Web and Extension Rewiring

**Files:**
- Create: `openapi/generate-client.mjs`, `src/openbiliclaw/web/js/api-client.js`, `extension/src/shared/api-client.ts`
- Modify: current web/extension request helpers, source dispatchers, settings screens, markup, and tests
- Delete: native-save, delight/notification, provider-editor, model-bundle, update, desktop-packaging controls and dispatch code

**Interfaces:**
- Produces generated shared API types/client from `openapi/openapi.json`.
- All extension source dispatchers claim and complete through `/api/v1/source-tasks`; web uses `/api/v1` and EventSource for progress/chat.

- [ ] Write failing generation checks and web/extension tests for health, onboarding, source status/task claim, profile, feed, interaction, library, chat SSE, alias health, LiteLLM Admin link, and every retained setting.
- [ ] Rewire existing markup/styles without React or visual redesign; remove dropped controls and duplicated request/state helpers.
- [ ] Delete native account save and proactive notification code from the extension; preserve passive activity capture and authenticated source execution.
- [ ] Run client generation diff check, extension typecheck/tests, and web smoke tests.
- [ ] Commit with `refactor: rewire web and extension to vnext api`.

### Task 23: Legacy Deletion, Installers, Documentation, and Full Verification

**Files:**
- Delete: orphaned Soul, legacy LLM/model-config, awareness/insight/probe, saved-sync, optimizer/persona, OpenClaw, self-update, desktop packaging, monolithic storage/runtime modules and obsolete tests
- Modify: `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `config.example.toml`, `docs/architecture.md`, `docs/spec.md`, `docs/cli.md`, `docs/configuration.md`, `docs/docker-deployment.md`, `docs/installation.md`, `docs/platform-source-integration.md`, installer and E2E runbooks

**Interfaces:**
- Leaves one authoritative backend and a fresh `data/vnext/openbiliclaw.db`; old data paths are read-only/manual archive with no import logic.

- [ ] Add failing repository policy tests for forbidden provider SDK imports, platform conditionals outside source adapters, profile JSON writes, raw SQL outside persistence/migrations, app-factory workflows, obsolete endpoints/commands, and desktop artifacts.
- [ ] Delete all unreachable legacy code/tests and simplify dependencies/config/installers around Docker-primary and source-plus-external-LiteLLM modes.
- [ ] Update every mandatory document and Mermaid diagram to match shipped code; add first-run and manual Docker/web/extension E2E runbooks.
- [ ] Run `ruff format --check`, `ruff check`, strict MyPy, import-linter, non-live pytest with coverage, extension typecheck/tests, OpenAPI client generation check, Alembic tests, Compose config, and image builds where locally available.
- [ ] Record any environment-blocked live E2E separately; do not claim it ran.
- [ ] Commit with `refactor: remove legacy architecture and document vnext`.
