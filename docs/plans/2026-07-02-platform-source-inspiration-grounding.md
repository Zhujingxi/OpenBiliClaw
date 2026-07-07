# Platform Source Inspiration Grounding Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reuse user-enabled platform sources as inspiration-only grounding backends for discovery keyword generation.

**Architecture:** Add a `platform_sources` inspiration backend that wraps synchronous existing platform search capabilities and maps result rows to `ExaPreviewItem` previews. It is part of the same provider chain as Exa / You.com and never writes search results into candidate or recommendation storage.

**Tech Stack:** Python dataclasses, existing Bilibili API client, existing YouTube scraper client, existing Reddit command utilities, pytest, Ruff, MyPy.

---

### Task 1: Platform Source Provider Tests

**Files:**
- Modify: `tests/test_discovery_inspiration_provider.py`

**Step 1: Write failing tests**

Add tests for:

- `PlatformSourceInspirationProvider` uses only enabled source providers.
- Result rows from Bilibili / YouTube / Reddit map to `ExaPreviewItem`.
- Per-query platform fanout is capped.
- Failures from one platform do not fail the whole search.

**Step 2: Run tests**

Run:

```bash
uv run --extra dev pytest tests/test_discovery_inspiration_provider.py -q
```

Expected: fail because `PlatformSourceInspirationProvider` does not exist.

### Task 2: Provider Implementation

**Files:**
- Modify: `src/openbiliclaw/discovery/inspiration_provider.py`

**Step 1: Implement minimal classes**

Add:

- `PlatformSearchBackend` protocol.
- `BilibiliPlatformSearchBackend`.
- `YoutubePlatformSearchBackend`.
- `RedditPlatformSearchBackend`.
- `PlatformSourceInspirationProvider`.

Each backend exposes `platform` and `search(query, limit)`.

**Step 2: Run provider tests**

Run:

```bash
uv run --extra dev pytest tests/test_discovery_inspiration_provider.py -q
```

Expected: pass.

### Task 3: Config And Runtime Factory

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/cli.py`
- Modify: `config.example.toml`
- Modify: `tests/test_config.py`

**Step 1: Write failing config tests**

Assert default `inspiration_search_backends` includes `platform_sources` before
remote providers, and aliases parse correctly.

**Step 2: Implement factory wiring**

`build_inspiration_search_provider()` accepts optional platform backend objects.
Runtime and dry-run construct available backends only for enabled sources:
Bilibili, YouTube, Reddit.

**Step 3: Run config and CLI tests**

Run:

```bash
uv run --extra dev pytest tests/test_config.py tests/test_cli.py::test_keyword_inspiration_dry_run_command_is_registered -q
```

Expected: pass.

### Task 4: Documentation And Verification

**Files:**
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `docs/changelog.md`
- Modify: `README.md`
- Modify: `README_EN.md`

**Step 1: Update docs**

Describe `platform_sources` as enabled-source, inspiration-only grounding.

**Step 2: Run verification**

Run:

```bash
uv run --extra dev pytest tests/test_discovery_inspiration.py tests/test_discovery_inspiration_provider.py tests/test_keyword_planner.py tests/test_config.py tests/test_cli.py::test_keyword_inspiration_dry_run_command_is_registered tests/test_llm_module_routing_e2e.py -q
uv run --extra dev ruff check src/openbiliclaw/discovery/inspiration_provider.py src/openbiliclaw/config.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/cli.py src/openbiliclaw/runtime/keyword_planner.py tests/test_discovery_inspiration_provider.py tests/test_config.py
uv run --extra dev mypy src/openbiliclaw/discovery/inspiration_provider.py src/openbiliclaw/config.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/cli.py src/openbiliclaw/runtime/keyword_planner.py
git diff --check
```

Expected: all pass.
