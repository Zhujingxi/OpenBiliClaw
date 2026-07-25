# Inspiration Multipage Platform Backends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Implemented and verified on 2026-07-03.

**Goal:** Let query inspiration grounding read more than one page of search results and use additional platform sources as inspiration-only evidence: Douyin direct-client, X/Twitter cookie replay, and injectable Xiaohongshu / Zhihu bridges.

**Architecture:** Keep `InspirationSearchProvider.search(query, limit=...)` as the planner-facing interface, and hide page fan-out inside platform backends. Add one config knob for page count; default it to `1` so default cost is unchanged. Platform backends return only `ExaPreviewItem` evidence and never write `discovery_candidates`. Xiaohongshu and Zhihu are bridge-only in this plan: keyword planning must not enqueue plugin/browser tasks unless a caller explicitly supplies a search bridge.

**Tech Stack:** Python dataclasses, existing source clients / task queues, pytest, Ruff, MyPy.

---

### Task 1: Config Knob

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `config.example.toml`
- Test: `tests/test_config.py`

- [x] Add `inspiration_search_pages_per_probe` defaulting to `1`, clamp to `1..5`, render it in saved config, and document it in the example config.
- [x] Update config tests for defaults, TOML load, clamping, save/load, and rendered TOML.

### Task 2: Multipage Backend Support

**Files:**
- Modify: `src/openbiliclaw/discovery/inspiration_provider.py`
- Test: `tests/test_discovery_inspiration_provider.py`

- [x] Add a `pages_per_probe` constructor argument to `PlatformSourceInspirationProvider`.
- [x] Pass `pages_per_probe` into platform backend calls when the backend accepts it; fall back to the existing two-argument call for old/fake backends.
- [x] Update Bilibili backend to call `client.search(..., page=1..N)` and dedupe previews.
- [x] Keep YouTube / Reddit compatible by increasing the limit when no explicit page parameter exists.

### Task 3: Add Douyin / X / Zhihu / XHS Inspiration Backends

**Files:**
- Modify: `src/openbiliclaw/discovery/inspiration_provider.py`
- Test: `tests/test_discovery_inspiration_provider.py`

- [x] Add `DouyinPlatformSearchBackend` wrapping a client with `search_aweme()`.
- [x] Add `XPlatformSearchBackend` wrapping `XClient.search(query, limit, product="Top")`.
- [x] Add `ZhihuPlatformSearchBackend` wrapping an async search callable or task bridge result.
- [x] Add `XhsPlatformSearchBackend` wrapping an async search callable or task bridge result.
- [x] Normalize each platform's raw rows into `ExaPreviewItem` with title, URL, and highlights.
- [x] Mark risk-controlled login/cookie replay sources appropriately: Bilibili, Douyin direct, and X.

### Task 4: Wire Runtime Construction

**Files:**
- Modify: `src/openbiliclaw/discovery/inspiration_provider.py`
- Modify: `src/openbiliclaw/cli.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Test: existing focused tests plus type check.

- [x] Extend `build_platform_source_backends()` parameters for douyin / x / zhihu / xhs clients or search callables.
- [x] Pass `inspiration_search_pages_per_probe` from CLI dry-run construction.
- [x] Pass the same config from runtime construction where inspiration provider is built.
- [x] Pass the existing X client into inspiration provider construction in runtime and dry-run CLI.
- [x] Do not enqueue xhs/zhihu async browser tasks from keyword planning unless an explicit bridge is supplied; avoid blocking planner on extension state by default.

### Task 5: Docs And Verification

**Files:**
- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`

- [x] Document the new page-count knob and supported inspiration platform sources.
- [x] Update docs/spec/changelog/README wording so generic platform-native terms are allowed to reach the AI curator rather than being hard-rejected.
- [x] Run `ruff format`, `ruff check`, `mypy`, focused pytest, and one dry-run smoke if credentials/providers are available.

### Verification Results

- `uv run --extra dev ruff check src/openbiliclaw/discovery/inspiration_provider.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/cli.py tests/test_discovery_inspiration_provider.py`
- `uv run --extra dev mypy src/openbiliclaw/discovery/inspiration_provider.py src/openbiliclaw/api/runtime_context.py src/openbiliclaw/cli.py`
- `uv run --extra dev pytest tests/test_discovery_inspiration_provider.py tests/test_config.py tests/test_keyword_planner.py -q` → `251 passed`
- `uv run --extra dev pytest -q` → `3309 passed, 32 skipped`
- Real dry-run with X temporarily enabled and real cookie replay: `grounding_ledger.searches=12`, `platforms={"twitter": 4, "reddit": 12}`, `timeouts=0`.
- Shifted-interest dry-run, with prior top interests cooled in memory only, selected `游戏资讯与推荐 / 漫画 / 科技新闻 / 气候变化` and produced all-platform keyword lists.
