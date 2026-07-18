# 6.2 朋友式推荐表达 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为推荐结果生成像朋友一样的自然语言表达，并把 CLI `recommend` 升级为真实展示入口。

**Architecture:** 在 `RecommendationEngine` 中增加结构化表达生成与历史回写，数据库补齐推荐记录更新接口，CLI 负责读取推荐并在展示后更新 `presented` 状态。

**Tech Stack:** Python 3.13, pytest, mypy, Ruff, Typer, Rich, existing recommendation/database infrastructure.

---

### Task 1: 为数据库推荐记录更新接口写失败测试

**Files:**
- Modify: `tests/test_storage.py`
- Modify: `src/openbiliclaw/storage/database.py`

**Step 1: Write the failing test**

新增测试，验证：
- `update_recommendation_content()` 能更新 `expression` 和 `topic`
- `mark_recommendations_presented()` 能把 `presented` 设为 1 并写 `presented_at`

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_storage.py::TestDatabase::test_update_recommendation_content tests/test_storage.py::TestDatabase::test_mark_recommendations_presented -q`

Expected: FAIL because these methods do not exist yet.

**Step 3: Write minimal implementation**

实现两个数据库方法。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_storage.py src/openbiliclaw/storage/database.py
git commit -m "feat: add recommendation history updates"
```

### Task 2: 为 RecommendationEngine 表达生成写失败测试

**Files:**
- Modify: `tests/test_recommendation_engine.py`
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Modify: `src/openbiliclaw/llm/prompts.py`

**Step 1: Write the failing test**

新增测试，验证：
- `generate_expression()` 能解析 LLM JSON
- `generate_recommendations()` 会把 `expression/topic_label` 填入结果
- 同时把表达回写到数据库记录

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_recommendation_engine.py::test_generate_expression_populates_recommendation_fields -q`

Expected: FAIL because expression generation is still a stub.

**Step 3: Write minimal implementation**

补 prompt builder、结构化解析和数据库回写。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_recommendation_engine.py src/openbiliclaw/recommendation/engine.py src/openbiliclaw/llm/prompts.py
git commit -m "feat: generate friend style recommendation expressions"
```

### Task 3: 为 CLI recommend 真实展示写失败测试

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Write the failing test**

新增测试，验证：
- 无推荐时 `recommend` 提示先执行 `discover`
- 有推荐时输出标题与推荐理由
- 展示后对应 recommendation 被标记为 `presented`

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_cli.py::test_recommend_displays_friend_style_recommendations -q`

Expected: FAIL because CLI is still a stub.

**Step 3: Write minimal implementation**

让 CLI 调用真实 recommendation 流程并展示结果。

**Step 4: Run test to verify it passes**

Run the same pytest command and verify PASS.

**Step 5: Commit**

```bash
git add tests/test_cli.py src/openbiliclaw/cli.py
git commit -m "feat: show recommendations in cli"
```

### Task 4: 更新文档

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

把 `6.2` checklist 更新为完成，并补 recommendation / CLI 模块文档。

**Step 2: Verify docs**

Run: `rg -n "6\\.2|朋友式推荐表达|recommend" docs/v0.1-todolist.md docs/modules/recommendation.md docs/modules/cli.md docs/changelog.md`

Expected: updated references appear in all four files.

**Step 3: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/recommendation.md docs/modules/cli.md docs/changelog.md
git commit -m "docs: update recommendation expression docs"
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
