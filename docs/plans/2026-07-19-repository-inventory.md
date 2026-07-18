# OpenBiliClaw Repository Inventory — Architecture & Refactor Targets

**Generated**: 2026-07-19 | **Workspace**: `$REPO_ROOT`（即本文件向上两级目录的 Git 仓库根）

> **说明**：本清单是**某一时点的发现性快照**（point-in-time discovery snapshot），用于记录当时测得的规模、热点与候选重构方向。其中的建议（如立即提取 `BaseProducer` 抽象类、常量集中等）为**非规范性输入**；与之冲突时，以 [`2026-07-19-incremental-architecture-refactor-plan.md`](2026-07-19-incremental-architecture-refactor-plan.md) 中的架构决策为准——后者有意选择 protocol/组合而非先验基类、按语义归属而非大一统集中。

---

## 1. Project Summary

OpenBiliClaw is an AI-powered, cross-platform content discovery agent (v0.3.168, pre-alpha). It builds user behavioral profiles ("Soul"), discovers content across 7+ platforms, and delivers personalized recommendations with natural-language explanations. Bilingual (Chinese primary, English supported). MIT licensed.

- **Homepage**: https://github.com/whiteguo233/OpenBiliClaw
- **Python**: >=3.11 | **Node**: extension only | **DB**: SQLite

---

## 2. Scale & Composition

| Language | Files | Code Lines | % of Code |
|----------|-------|-----------|-----------|
| Python | 500 | 187,748 | 64.1% |
| TypeScript | 155 | 32,113 | 67.6% |
| JavaScript+Genshi | 29 | 19,261 | 60.2% |
| HTML | 10 | 10,166 | 79.0% |
| CSS+Lasso | 2 | 3,231 | 80.8% |
| JavaScript | 21 | 2,804 | 67.4% |
| YAML | 11 | 1,113 | — |
| Bash/PS | 5 | 1,360 | — |
| TOML | 3 | 410 | — |
| Markdown | 430 | 0 (doc) | — |
| **Total** | **1,251** | **260,842** | — |

- **Python tests**: 254 files, ~4,928 test functions/classes, ~92K lines
- **Extension tests**: 90 TypeScript test files
- **Extension popup**: 14,366 lines of hand-written JS (vanilla, no framework)

---

## 3. Directory Structure (Key Areas)

```
main/
├── src/openbiliclaw/           # Python backend (187K LOC)
│   ├── cli.py                  # CLI entry (9,253 lines)  ← OVERSIZED
│   ├── cli_models.py           # CLI sub-commands (1,989)
│   ├── config.py               # Config system (2,801)
│   ├── api/                    # FastAPI HTTP backend (17,252)
│   │   └── app.py              # Monolithic app (11,371)  ← OVERSIZED
│   ├── storage/                # SQLite persistence (12,526)
│   │   └── database.py         # Monolithic DB (11,860)   ← OVERSIZED
│   ├── runtime/                # Runtime daemon services (15,736)
│   │   ├── *_producer.py       # 7 platform producers
│   │   ├── refresh.py          # (3,216)  ← OVERSIZED
│   │   └── keyword_planner.py  # Unified keyword planner
│   ├── discovery/              # Content discovery (12,593)
│   │   ├── engine.py           # (3,059)  ← OVERSIZED
│   │   └── strategies/         # Platform-specific strategies
│   ├── soul/                   # User profiling engine (14,385)
│   │   ├── speculator.py       # Proactive speculation
│   │   ├── preference_analyzer.py
│   │   └── profile.py / profile_builder.py
│   ├── llm/                    # LLM abstraction layer (10,349)
│   │   ├── *_provider.py       # OpenAI, Anthropic, Gemini, Ollama providers
│   │   ├── service.py          # Routing + fallback
│   │   └── prompts.py          # Prompt templates
│   ├── recommendation/         # Recommendation engine (4,373)
│   │   └── engine.py           # (3,317)  ← OVERSIZED
│   ├── model_config/           # Model config CRUD + migration (5,751)
│   ├── sources/                # Platform adapters + task types (6,983)
│   ├── saved_sync/             # Browser save-to-account sync (1,564)
│   ├── eval/                   # Evaluation/optimization framework (6,013)
│   ├── integrations/           # OpenClaw integration (1,862)
│   ├── bilibili/               # Bilibili API + auth (1,411)
│   ├── youtube/                # YouTube client (1,078)
│   ├── memory/                 # Memory management (1,010)
│   ├── agent/                  # Agent orchestration (234)
│   └── web/                    # PWA frontend (JS/CSS/HTML)
│       ├── desktop/            # Desktop web UI
│       ├── js/                 # Mobile web + shared JS
│       ├── shared/             # Shared JS (model-config, saved-sync)
│       └── setup/              # Web setup wizard
├── extension/                  # Chrome/Firefox extension (TS)
│   ├── src/                    # TypeScript source (21K lines)
│   │   ├── background/         # Service worker + dispatchers
│   │   ├── content/            # Content scripts per platform
│   │   ├── main/               # World-main injection scripts
│   │   └── shared/             # Shared types + utilities
│   ├── popup/                  # Extension popup (14K lines JS)
│   ├── dist/                   # Compiled output
│   ├── tests/                  # 90 test files
│   └── scripts/                # Build/release tooling
├── tests/                      # Python tests (254 files)
├── scripts/                    # Build/eval/release scripts (22 files)
├── packaging/                  # Desktop installer (PyInstaller)
├── docs/                       # Documentation (~400+ markdown files)
│   ├── modules/                # Per-module docs
│   └── plans/                  # Design/plan documents
├── data/                       # Runtime data (SQLite DB + backups + image cache)
├── docker/                     # Docker files
├── pyproject.toml              # Build config + deps
├── config.example.toml         # Config reference (417 lines, 25 sections)
├── CLAUDE.md                   # Agent guidance
└── AGENTS.md                   # Non-Claude agent guidance
```

---

## 4. Entry Points

| Entry | File | Lines | Description |
|-------|------|-------|-------------|
| CLI | `cli.py` | 9,253 | Typer app, `openbiliclaw` command |
| API server | `api/app.py` | 11,371 | FastAPI, `openbiliclaw serve-api` |
| Extension | `extension/src/background/service-worker.ts` | ~200 | MV3 service worker entry |
| Desktop app | `packaging/entry.py` | ~1,200 | PyInstaller-packaged desktop tray app |
| Web UI | `web/index.html` | — | PWA served by API at `/` |

CLI commands: `start`, `init`, `recommend`, `profile`, `config-show`, `serve-api`, plus sub-groups for `models`.

---

## 5. Backend / Frontend Boundaries

### Backend (Python/FastAPI)
- **REST API**: `api/app.py` — monolithic single-file FastAPI app serving ~50+ endpoints
- **WebSocket**: Runtime stream for real-time updates to extension/web UI
- **Auth**: Bearer token auth via `api/auth.py`, device-code flow for extension pairing
- **Persistence**: SQLite via `storage/database.py`, JSON state files via `memory/json_state.py`
- **Configuration**: TOML files read by `config.py`, with environment variable overrides
- **Scheduling**: APScheduler in `runtime/` for background content refresh

### Frontend (3 surfaces)
1. **Extension popup** — vanilla JS, served from `extension/popup/`, communicates with backend via REST + WebSocket
2. **Desktop web UI** (`web/desktop/`) — vanilla JS SPA, served as static files from API server
3. **Mobile web UI** (`web/js/`) — vanilla JS SPA, responsive PWA
4. **Web setup wizard** (`web/setup/`) — guided first-run configuration

### Extension Architecture
- **Background**: Service worker (`service-worker.ts`) dispatches tasks to platform-specific content scripts
- **Content scripts**: Per-platform scripts inject into target pages (Bilibili, Douyin, X, etc.)
- **World-main scripts**: Injected into page context for API interception (X GraphQL tap, Douyin fetch tap, XHS state bridge)
- **Native save**: Extension can perform cross-platform "save to account" actions

---

## 6. Dependency Graph (Top Internal Imports)

```
llm.json_utils         ← 18 consumers (most coupled utility)
sources.platforms      ← 17 consumers
llm.task_options       ← 17 consumers
llm.base               ← 15 consumers
discovery.engine       ← 13 consumers
llm.prompts            ← 12 consumers
saved_sync.identity    ← 6 consumers
llm.service            ← 6 consumers
config                 ← 6 consumers
runtime.keyword_fetch  ← 10 consumers
```

Key external dependencies: `httpx`, `fastapi`, `pydantic`, `openai`, `anthropic`, `google-genai`, `typer`, `rich`, `apscheduler`, `uvicorn`, `bilibili-api-python`, `twitter-cli`, `rdt-cli`, `yt-dlp`, `Pillow`, `websockets`.

---

## 7. Oversized Files (High-Value Refactor Targets)

| File | Lines | Risk | Refactor Strategy |
|------|-------|------|-------------------|
| `storage/database.py` | 11,860 | Medium | Split into domain-specific DAOs: events, content_cache, recommendations, discoveries, saved_sync, llm_usage |
| `api/app.py` | 11,371 | High | Split into route modules (bilibili, extension, recommendations, saved_sync, config, auth, streaming) |
| `cli.py` | 9,253 | Low | Extract command groups into separate modules; use Typer's native sub-command pattern |
| `recommendation/engine.py` | 3,317 | Medium | Extract scoring, ranking, explanation generation into separate modules |
| `runtime/refresh.py` | 3,216 | Medium | Split by concern: platform-specific refresh, pool management, scheduling |
| `discovery/engine.py` | 3,059 | Medium | Extract strategy dispatch, candidate evaluation, pipeline orchestration |
| `config.py` | 2,801 | Medium | Split into section-specific config modules (api, sources, scheduler, discovery, storage) |
| `soul/speculator.py` | 1,976 | Low | Extract hypothesis generation, testing, and feedback analysis |

### Oversized Test Files
| File | Lines |
|------|-------|
| `test_api_app.py` | 13,870 |
| `test_cli.py` | 7,537 |
| `test_refresh_runtime.py` | 4,952 |
| `test_recommendation_engine.py` | 4,563 |
| `test_storage.py` | 3,809 |
| `test_keyword_planner.py` | 3,191 |
| `test_discovery_engine.py` | 3,153 |
| `test_pipeline_advanced.py` | 3,053 |

These mirror the oversized source files and would naturally split alongside them.

---

## 8. Duplicated Patterns (DRY Violations)

1. **`_ensure_*_columns` methods** in `database.py`: ~15 nearly-identical column migration methods (lines 5979–6333). Each uses `PRAGMA table_info(...)` to find missing columns, then runs `ALTER TABLE ... ADD COLUMN` for those absent. Should be data-driven from a schema definition.

2. **`_ensure_ledger_table` methods**: `x_producer.py:303`, `reddit_producer.py:618`, `youtube_producer.py:260` — identical pattern. Extract to a shared base class or utility.

3. **`_truncate*` functions**: Three different implementations across `negative_exemplars.py`, `preference_analyzer.py`, and `x_normalize.py`. Consolidate into shared utility.

4. **`DEFAULT_*` constants**: Scattered across `config.py`, `sources/event_format.py`, `soul/speculator.py`, `soul/preference_analyzer.py`, `storage/maintenance.py`, `youtube/client.py`. Centralize in config.

5. **Producer pattern**: 7 platform-specific producers (`bilibili_producer.py`, `douyin_producer.py`, `reddit_producer.py`, `x_producer.py`, `xhs_producer.py`, `youtube_producer.py`, `zhihu_producer.py`) share common lifecycle/state patterns. A `BaseProducer` abstract class would reduce duplication.

6. **Extension platform dispatchers**: 7 nearly-identical task dispatchers in `extension/src/background/*-task-dispatcher.ts`. Shared base pattern exists but duplicated boilerplate remains.

---

## 9. Configuration Surface

`config.example.toml` has 25 top-level sections:
`[general]`, `[api]`, `[api.auth]`, `[models]`, `[models.chat]`, `[models.embedding]`, `[bilibili]`, `[bilibili.browser]`, `[network]`, `[sources.browser]`, `[sources.bilibili]`, `[sources.xiaohongshu]`, `[sources.douyin]`, `[sources.youtube]`, `[sources.twitter]`, `[sources.zhihu]`, `[sources.reddit]`, `[scheduler]`, `[scheduler.pool_source_shares]`, `[discovery]`, `[autostart]`, `[saved_sync]`, `[storage]`, `[logging]`, `[soul.preference]`

The `model_config/` module (5,751 lines) adds a separate configuration subsystem for LLM model management with its own migration, serialization, validation, and revision tracking. This is architecturally distinct from the TOML config and represents a dual config path.

---

## 10. Persistence Layer

- **Primary**: SQLite via `storage/database.py` (11,860 lines). Single class `Database` with ~100+ methods. No ORM — raw SQL with `sqlite3`.
- **Secondary**: JSON files via `memory/json_state.py` for user profile state.
- **Image cache**: Filesystem-based in `data/image-cache/`.
- **Backups**: SQLite backup to `data/backups/` via `storage/maintenance.py`.

Schema is migrated inline via `_ensure_*_columns` methods rather than a formal migration system.

---

## 11. Test Infrastructure

- **Python**: pytest with pytest-asyncio, pytest-cov. 254 test files, ~4,928 test functions/classes. Coverage target: 70%+. Integration tests marked with `@pytest.mark.integration`.
- **Extension**: Node built-in test runner (`node --test`), 90 test files.
- **Lint/Type**: Python: ruff format + check, mypy strict. Extension: TypeScript typecheck via `tsc --noEmit`.
- **E2E**: Separate test files for browser extension E2E flows (`test_bili_extension_browser_e2e.py`, `test_phase7_e2e.py`).
- **Evaluation framework**: `eval/` module provides persona-based evaluation, auto-optimization loops, and speculation evaluation.

### Lint/Type Status (from prior audits):
- **ruff**: Passes
- **mypy**: 10 errors in `cli_models.py` (non-functional — Typer decorator compatibility)
- **pytest**: 5,599 passed, 1 failed (as of 2026-07-19)

---

## 12. Compatibility Risks

1. **Monolithic API app**: `api/app.py` is 11K lines. Any refactor touching it risks breaking extension protocol, web UI, or CLI integration. Split along route boundaries first.

2. **Tight coupling to `database.py`**: Nearly every module imports from `storage/database.py`. Splitting it requires careful interface extraction.

3. **Extension protocol**: The extension↔backend protocol uses WebSocket + REST with ad-hoc message formats. Breaking changes must be coordinated across extension release.

4. **Dual config paths**: Model configs are managed separately from TOML config, using a different serialization format. Unifying these carries migration complexity.

5. **Chinese-language coupling**: UI strings, CLI messages, and prompt templates are mixed Chinese/English. Internationalization would be a separate undertaking.

6. **Desktop packaging**: PyInstaller-based macOS/Windows bundling via `packaging/`. Changes to import paths or static file locations can break packaging.

---

## 13. Safest High-Value Refactors (Recommended Order)

| Priority | Target | Effort | Risk | Rationale |
|----------|--------|--------|------|-----------|
| P0 | Extract `api/app.py` route modules | Medium | Medium | Biggest monolithic file; splitting by route is low-logic-change |
| P0 | Data-drive `_ensure_*_columns` in `database.py` | Small | Low | Eliminates ~15 boilerplate methods with no behavior change |
| P1 | Split `database.py` into domain DAOs | Large | Medium | Enables parallel dev; defers to schema-stable extraction |
| P1 | Extract `BaseProducer` abstract class | Small | Low | 7 producers share identical lifecycle; pure extraction |
| P2 | Split `cli.py` into sub-command modules | Medium | Low | Typer's native sub-command pattern; well-tested via CLI tests |
| P2 | Centralize `DEFAULT_*` and `_truncate*` utilities | Small | Low | Consolidation with no logic change |
| P3 | Split `config.py` into section modules | Medium | Medium | Many consumers import from config; extract incrementally |
| P3 | Split remaining engines (`recommendation`, `discovery`, `refresh`) | Medium | Medium | Each has clear internal boundaries |

---

## 14. Build / Test / Lint Commands

### Python Backend
```bash
pip install -e ".[dev]"
pytest                                    # All tests (5,599 pass)
pytest --cov=openbiliclaw                 # With coverage
ruff format src/ tests/                   # Format
ruff check src/ tests/                    # Lint
mypy src/                                 # Type check (strict)
```

### Browser Extension
```bash
cd extension
npm run build                             # Clean + types + bundle
npm run typecheck                         # TypeScript check
npm run test                              # Node test runner
```

### Docker
```bash
docker compose up -d --build              # Backend on port 8420
```

### CLI Quick-test
```bash
openbiliclaw start
openbiliclaw recommend
openbiliclaw profile
openbiliclaw config-show
openbiliclaw serve-api
```
