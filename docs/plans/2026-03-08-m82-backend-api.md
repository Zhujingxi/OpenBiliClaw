# M82 Backend API Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现浏览器插件可直接联调的最小后端 API，提供 `GET /api/health`、`POST /api/events`、`GET /api/recommendations`，并让 `openbiliclaw start` 至少能启动这个本地服务。

**Architecture:** 使用 FastAPI 增加轻量 API 层，复用现有 `MemoryManager` 与 `Database`。先用路由测试锁住接口形态，再实现 API app，最后把 `start` 接成 API 启动入口并同步文档。

**Tech Stack:** Python 3.13, FastAPI, Pydantic, Typer, pytest, mypy, Ruff

---

### Task 1: Add failing API tests

**Files:**
- Create: `tests/test_api_app.py`

**Step 1: Write the failing tests**

新增路由测试：

```python
def test_health_endpoint_returns_ok() -> None: ...
def test_events_endpoint_persists_batch() -> None: ...
def test_recommendations_endpoint_returns_items() -> None: ...
```

断言重点：

- `/api/health` 返回 200 和 `{"status": "ok"}`
- `/api/events` 能接收 `{"events": [...]}` 并写入 `MemoryManager`
- `/api/recommendations` 返回列表，每项包含 `id` / `title`

**Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_api_app.py -v`

Expected: FAIL because API app does not exist yet.

### Task 2: Implement FastAPI app and schemas

**Files:**
- Create: `src/openbiliclaw/api/app.py`
- Create: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/storage/database.py` if needed

**Step 1: Write minimal implementation**

在 `src/openbiliclaw/api/models.py` 中定义：

- `BehaviorEventIn`
- `BehaviorEventBatchIn`
- `HealthResponse`
- `RecommendationOut`

在 `src/openbiliclaw/api/app.py` 中实现：

- `create_app(...)`
- `GET /api/health`
- `POST /api/events`
- `GET /api/recommendations`

`POST /api/events` 复用 `MemoryManager.propagate_event()`。

`GET /api/recommendations` 复用数据库查询结果，并映射为 API 输出结构。

**Step 2: Run focused tests to verify they pass**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_api_app.py -v`

Expected: PASS

**Step 3: Commit**

```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py tests/test_api_app.py src/openbiliclaw/storage/database.py
git commit -m "feat: add backend api app"
```

### Task 3: Hook CLI start to run API server

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Write the failing CLI test**

在 `tests/test_cli.py` 新增：

```python
def test_start_runs_api_server_entrypoint(...) -> None: ...
```

断言：

- `start` 不再输出纯 stub
- 会调用 API server 启动入口

通过 monkeypatch 一个 fake `run_api_server()` 或等价函数来验证调用。

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py::test_start_runs_api_server_entrypoint -v`

Expected: FAIL because `start` is still a placeholder.

**Step 3: Write minimal implementation**

在 `src/openbiliclaw/cli.py` 中：

- 增加 `_run_api_server(host="127.0.0.1", port=8420)` helper
- `start()` 改为显示启动信息并调用 `_run_api_server()`

实现只需能启动 API，不需要完整 agent runtime。

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py::test_start_runs_api_server_entrypoint -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py
git commit -m "feat: start backend api from cli"
```

### Task 4: Update docs for M82

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/changelog.md`
- Modify: `docs/modules/cli.md`

**Step 1: Update docs**

同步：

- `docs/v0.1-todolist.md`：标记 `8.2` 的完成项
- `docs/changelog.md`：追加 `8.2 后端 API` 条目
- `docs/modules/cli.md`：说明 `start` 现在会启动本地 API 服务

**Step 2: Review docs diff**

Run: `git diff -- docs/v0.1-todolist.md docs/changelog.md docs/modules/cli.md`

Expected: Only M82-related documentation changes.

**Step 3: Commit**

```bash
git add docs/v0.1-todolist.md docs/changelog.md docs/modules/cli.md
git commit -m "docs: update backend api status"
```

### Task 5: Run full verification

**Files:**
- Verify: `src/openbiliclaw/api/app.py`
- Verify: `src/openbiliclaw/api/models.py`
- Verify: `src/openbiliclaw/cli.py`
- Verify: `tests/test_api_app.py`
- Verify: `tests/test_cli.py`

**Step 1: Run Ruff**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/`

Expected: `All checks passed!`

**Step 2: Run mypy**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/`

Expected: `Success: no issues found ...`

**Step 3: Run pytest**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q`

Expected: All tests pass.

**Step 4: Commit any remaining fixups**

如果验证中出现小修复，单独提交：

```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py src/openbiliclaw/cli.py tests/test_api_app.py tests/test_cli.py docs/v0.1-todolist.md docs/changelog.md docs/modules/cli.md
git commit -m "fix: polish backend api integration"
```

**Step 5: Prepare branch for integration**

Run:

```bash
git status --short
git log --oneline --decorate -5
```

Expected: branch ready for review or merge.
