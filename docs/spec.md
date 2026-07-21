# OpenBiliClaw vNext system specification

This document is the current product and backend contract. It does not describe compatibility behavior. Historical v0.3 specifications are available through Git history and historical entries in `docs/changelog.md`.

## 1. Product scope

Retained clients are the existing static Web application and browser extension. There is no desktop application. Backend correctness and maintainability take priority over visual redesign.

Retained user journey:

1. complete first-run system checks;
2. connect one or more supported sources;
3. bootstrap normalized activity evidence;
4. build and edit a revisioned evidence profile;
5. replenish and browse a discovery feed;
6. submit feedback that affects later ranking;
7. chat through a streamed typed AI task;
8. use local favorites and watch later.

Supported built-in source IDs are `bilibili`, `xiaohongshu`, `douyin`, `youtube`, `twitter`, `zhihu`, and `reddit`. Each source exposes only its implemented discovery/bootstrap capabilities.

## 2. System architecture

```text
Web / extension ─► FastAPI /api/v1 routers ─► application use cases
                         │ SSE                    │
                         │                        ├─► domain policies
Huey worker/scheduler ────────────────────────────┤
                                                  ├─► repositories ─► SQLite + Alembic
                                                  ├─► source connectors
                                                  └─► typed TaskRunner ─► PydanticAI ─► LiteLLM

Huey transport ─► separate SQLite queue
LiteLLM configuration ─► dedicated PostgreSQL
```

Features own their domain types, ports, use cases, router, and tests. Infrastructure implements ports. Workflows do not live in the app factory or routers.

## 3. Runtime contract

### 3.1 vNext

#### Domain contracts

`ActivityEvent` is the immutable normalized evidence record. `ProfileSignal` derives from an event without dropping provenance. `ProfileSnapshot` is revisioned and contains narrative, interests, avoidances, style preferences, values, source affinities, confidence, and evidence references. Explicit edits create high-confidence override evidence.

`ContentItem` is the normalized cross-source content identity. `CandidateAssessment` binds a typed assessment to content and profile revision. `FeedEntry` is an admitted item; `Interaction` records user feedback. Favorites and watch later are predefined local collections. Library reads return `LibraryItem (CollectionItem + ContentItem)` rather than source-specific save records.

AI may propose profile deltas, source-neutral keywords, batch candidate assessments, chat output, and recommendation explanations. Feed uses embeddings to derive bounded within-batch semantic diversity. Deterministic application policy owns query allocation, validation, deduplication, admission, diversity, novelty, scoring, transaction boundaries, and persistence.

#### AI execution

Every generative task has typed input/output, a reusable PydanticAI agent, stable model alias, semantic retry limit, timeout, usage limits, cache policy, and execution lane. Production features call the shared `TaskRunner`; they do not implement provider routing, HTTP retry, JSON repair, or fallback. A streaming run keeps the complete PydanticAI/AnyIO lifecycle in one producer task and crosses the public generator boundary only through validated typed snapshots; consumer-side `wait_for`, cancellation, or task changes cannot split context ownership.

Only `obc-interactive` and `obc-analysis` are valid generative aliases, and each task setting must retain the alias declared by its execution lane. Embeddings use the separate `obc-embedding` service and namespace vectors by alias, dimension, and profile version. Provider credentials and deployments exist only in LiteLLM. Product health is an alias-only redacted status; it never projects provider credentials or deployment payloads.

#### Jobs

The worker registers `source_sync`, `profile_projection`, `feed_replenishment`, and `cleanup`. Until onboarding completes, periodic `source_sync`, `profile_projection`, and `feed_replenishment` ticks are suppressed and do not create durable buckets; periodic `cleanup` remains active. Explicit onboarding/API scheduling bypasses that periodic gate. After completion, per-minute transport ticks resolve every interval persisted under `UserSettings.schedules` and normal job-specific idempotent time-bucket scheduling resumes. Business status and successful-continuation acknowledgement are persisted in `job_runs`; the live worker sweep retries an unacknowledged continuation without requiring restart. Operations and continuations are idempotent, so duplicate delivery and recovery must not duplicate feature effects. Interactive chat bypasses Huey and streams through SSE.

#### Persistence and configuration

The application database is a fresh Alembic-managed SQLite database at `data/vnext/openbiliclaw.db` by default. Huey uses a separate `data/vnext/huey.db`. Old data is left untouched and is never imported automatically.

Mutable product settings are strict nested data in the application database and are available through the existing settings UI and `/api/v1/settings`. Infrastructure secrets are environment-only. Source credentials are encrypted. Provider credentials remain exclusively in LiteLLM Admin.

#### Source contract

Each platform package exposes a `SourceManifest`, strict Pydantic settings, capabilities, and a `SourceConnector`. A connector returns only `ActivityEvent` or `ContentItem`. Unsupported operations are absent. Seven connectors are registered explicitly; there is no dynamic plugin framework.

Extension-executed source work uses generic typed claim/complete with deadline and lease semantics. No platform-specific task HTTP API or platform account mutation is supported.

#### HTTP and authentication

Public route groups are:

```text
/api/v1/auth
/api/v1/system
/api/v1/settings
/api/v1/onboarding
/api/v1/sources
/api/v1/source-tasks
/api/v1/events
/api/v1/profile
/api/v1/feed
/api/v1/interactions
/api/v1/library
/api/v1/chat
/api/v1/jobs
```

Web 使用 same-origin password→HttpOnly cookie；unsafe requests and the lease-mutating source-task claim GET also require the same-origin CSRF header. Extension origin 即使来自 loopback，也必须先把 provisioned device key exchange 为 finite bearer，不能继承 Web trust。Installer bearer access is a separate operational credential. Authentication failures, validation errors, and internal failures use the shared safe error envelope and never echo secrets or provider payloads.

Chat and onboarding/job progress use an authenticated fetch stream over SSE. The extension uses long-poll generic source-task claim and typed completion.

#### CLI

Only operational commands are public: `serve`, `worker`, `doctor`, `eval`, `db migrate`, and `db backup`. Product workflows are not CLI commands and there are no legacy aliases.

#### Acceptance

- strict MyPy, Ruff complexity ≤ 12, and import-linter pass;
- fresh migration and repository transaction/concurrency tests pass;
- seven-source connector contract and mocked transport tests pass;
- worker priority/idempotency/retry/cancel/recovery tests pass;
- OpenAPI client generation is deterministic;
- Web and extension retained journeys pass against the same backend;
- no provider SDK in features, platform conditional outside source packages, profile JSON write, raw SQL outside allowed persistence, workflow in app factory, obsolete command, compatibility endpoint, or desktop artifact remains;
- current installation and E2E runbooks are reproducible without live credentials unless explicitly stated.
