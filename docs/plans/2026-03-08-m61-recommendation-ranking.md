# 6.1 推荐排序 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现最小闭环的推荐排序：从 `content_cache` 选择未推荐内容，按相关性排序，返回 `Recommendation` 并写入推荐历史。

**Architecture:** 在 `Database` 中补齐未推荐查询与推荐历史读写接口，在 `RecommendationEngine` 中统一完成排序、结果构造和历史写入。调用层可显式传入 `discovered` 列表，也可直接从缓存读取。

**Tech Stack:** Python 3.13, pytest, mypy, Ruff, SQLite wrapper, existing discovery cache and recommendation dataclasses.

---

### Task 1: 为数据库的未推荐查询和推荐历史写失败测试

**Files:**
- Modify: `tests/test_storage.py`
- Modify: `src/openbiliclaw/storage/database.py`

**Step 1: Write the failing test**

新增测试，验证：
- `get_unrecommended_content(limit)` 只返回未在 `recommendations` 表出现过的内容
- `insert_recommendation()` 和 `get_recommendations()` 能正确写入和读回历史记录

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_storage.py::TestDatabase::test_get_unrecommended_content_excludes_history tests/test_storage.py::TestDatabase::test_insert_and_get_recommendations -q`

Expected: FAIL because these database helpers do not exist yet.

**Step 3: Write minimal implementation**

实现三个数据库方法：
- `get_unrecommended_content()`
- `insert_recommendation()`
- `get_recommendations()`

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_storage.py src/openbiliclaw/storage/database.py
git commit -m "feat: add recommendation storage queries"
```

### Task 2: 为 RecommendationEngine 排序与历史写入写失败测试

**Files:**
- Create: `tests/test_recommendation_engine.py`
- Modify: `src/openbiliclaw/recommendation/engine.py`

**Step 1: Write the failing test**

新增测试，验证：
- 传入 `discovered` 列表时按 `relevance_score` 排序
- 不传 `discovered` 时从 `content_cache` 读取
- 生成结果后写入推荐历史
- 第二次调用不会重复选中已入历史的内容

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_recommendation_engine.py -q`

Expected: FAIL because `RecommendationEngine.generate_recommendations()` is still a stub.

**Step 3: Write minimal implementation**

让 `RecommendationEngine` 接收 `database`，实现排序、推荐对象构造和历史写入。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_recommendation_engine.py src/openbiliclaw/recommendation/engine.py
git commit -m "feat: rank and record recommendations"
```

### Task 3: 更新模块文档与里程碑状态

**Files:**
- Create: `docs/modules/recommendation.md`
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

补最小模块文档，写清：
- `RecommendationEngine`
- `Recommendation`
- 当前只完成 6.1，6.2/6.3 仍未完成

并更新 todo 与 changelog。

**Step 2: Verify docs**

Run: `rg -n "6\\.1|RecommendationEngine|recommendations" docs/modules/recommendation.md docs/v0.1-todolist.md docs/changelog.md`

Expected: updated references appear in all three files.

**Step 3: Commit**

```bash
git add docs/modules/recommendation.md docs/v0.1-todolist.md docs/changelog.md
git commit -m "docs: update recommendation ranking docs"
```

### Task 4: 全量验证

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
