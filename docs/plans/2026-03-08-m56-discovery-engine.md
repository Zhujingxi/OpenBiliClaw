# 5.6 发现引擎编排 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 `ContentDiscoveryEngine` 真正承担发现编排职责：并发执行策略、统一去重排序，并把结果写入 `content_cache`。

**Architecture:** 通过 `asyncio.gather(return_exceptions=True)` 并发执行 discovery strategies，在引擎层按 `bvid` 合并高分结果，再统一排序裁剪并写入 SQLite 缓存。数据库提供只读缓存查询接口，支撑测试和后续推荐排序。

**Tech Stack:** Python 3.13, asyncio, pytest, mypy, Ruff, SQLite wrapper, existing discovery strategies.

---

### Task 1: 为并发执行与失败容错写失败测试

**Files:**
- Modify: `tests/test_discovery_engine.py`
- Modify: `src/openbiliclaw/discovery/engine.py`

**Step 1: Write the failing test**

新增测试，验证 `discover()` 会并发执行多个策略，并在单个策略失败时继续返回其它策略结果。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_discovery_engine.py::test_discovery_engine_runs_strategies_concurrently_and_tolerates_failures -q`

Expected: FAIL because engine currently executes strategies sequentially and has no explicit async fan-out behavior.

**Step 3: Write minimal implementation**

实现 `asyncio.gather(return_exceptions=True)` 并发执行与异常容错。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_discovery_engine.py src/openbiliclaw/discovery/engine.py
git commit -m "feat: run discovery strategies concurrently"
```

### Task 2: 为去重保留高分结果写失败测试

**Files:**
- Modify: `tests/test_discovery_engine.py`
- Modify: `src/openbiliclaw/discovery/engine.py`

**Step 1: Write the failing test**

新增测试，验证重复 `bvid` 时会保留高分版本而不是先到先得。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_discovery_engine.py::test_discovery_engine_keeps_highest_scored_duplicate -q`

Expected: FAIL because current dedupe keeps first occurrence.

**Step 3: Write minimal implementation**

实现“同 `bvid` 保留更高分对象”的合并逻辑。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_discovery_engine.py src/openbiliclaw/discovery/engine.py
git commit -m "feat: merge duplicate discovery results by score"
```

### Task 3: 为缓存写入和读取写失败测试

**Files:**
- Modify: `tests/test_storage.py`
- Modify: `tests/test_discovery_engine.py`
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `src/openbiliclaw/discovery/engine.py`

**Step 1: Write the failing test**

新增测试，验证 `discover()` 会把最终结果写入 `content_cache`，并能通过 `get_cached_content()` 读回。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_storage.py::test_get_cached_content_returns_cached_rows tests/test_discovery_engine.py::test_discovery_engine_caches_final_results -q`

Expected: FAIL because read-back helper and discover-side caching are not implemented.

**Step 3: Write minimal implementation**

实现 `Database.get_cached_content()` 和 engine 内缓存写入。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_storage.py tests/test_discovery_engine.py src/openbiliclaw/storage/database.py src/openbiliclaw/discovery/engine.py
git commit -m "feat: cache discovery results to sqlite"
```

### Task 4: 更新文档

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/discovery.md`
- Modify: `docs/changelog.md`

**Step 1: Update task status**

把 `5.6` checklist 更新为完成，并在 discovery 模块文档中补并发编排、去重和缓存写入行为。

**Step 2: Verify docs**

Run: `rg -n "5\\.6|content_cache|并行执行多个策略|缓存" docs/v0.1-todolist.md docs/modules/discovery.md docs/changelog.md`

Expected: updated references appear in all three files.

**Step 3: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/discovery.md docs/changelog.md
git commit -m "docs: update discovery orchestration docs"
```

### Task 5: 全量验证

**Files:**
- Verify only

**Step 1: Run Ruff**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

**Step 2: Run mypy**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/`

Expected: `Success: no issues found ...`

**Step 3: Run pytest**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q`

Expected: full suite passes.
