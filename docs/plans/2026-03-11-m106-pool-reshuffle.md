# 候选池即时出片 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 popup 的“立即刷新推荐”改成从 discovery pool 即时拣一批新内容，避免等待完整 discover + recommendation 文案流程。

**Architecture:** `content_cache` 作为长期候选池，`refresh` 负责补池子，`reshuffle` 负责即时从池子里挑新内容。推荐展示优先使用 `expression`，没有则回退到 `relevance_reason`。

**Tech Stack:** Python, FastAPI, SQLite, extension popup JS, existing discovery/recommendation engines

---

### Task 1: 池子状态与数据库查询

**Files:**
- Modify: `src/openbiliclaw/storage/database.py`
- Test: `tests/test_storage.py`

**Step 1: Write the failing test**
- 增加测试，验证可从 `content_cache` 中按 `fresh -> relevance_score -> last_scored_at` 取一批未展示、未反馈的候选。

**Step 2: Run test to verify it fails**
Run: `PYTHONPATH=src .venv/bin/pytest tests/test_storage.py -q`

**Step 3: Write minimal implementation**
- 增加池子状态字段 migration
- 增加 `get_pool_candidates()` / `mark_pool_items_shown()` 等最小接口

**Step 4: Run test to verify it passes**
Run: `PYTHONPATH=src .venv/bin/pytest tests/test_storage.py -q`

**Step 5: Commit**
```bash
git add src/openbiliclaw/storage/database.py tests/test_storage.py
git commit -m "feat: add discovery pool candidate selection"
```

### Task 2: RecommendationEngine 增加 reshuffle 快路径

**Files:**
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Test: `tests/test_recommendation_engine.py`

**Step 1: Write the failing test**
- 增加测试，验证 `reshuffle_recommendations()` 会直接从池子里挑新内容，并在没有 `expression` 时使用 `relevance_reason` 作为展示文案。

**Step 2: Run test to verify it fails**
Run: `PYTHONPATH=src .venv/bin/pytest tests/test_recommendation_engine.py -q`

**Step 3: Write minimal implementation**
- 增加 `reshuffle_recommendations(profile, limit)`
- 选出候选后写入 `recommendations`
- `expression` 缺失时回退 `relevance_reason`

**Step 4: Run test to verify it passes**
Run: `PYTHONPATH=src .venv/bin/pytest tests/test_recommendation_engine.py -q`

**Step 5: Commit**
```bash
git add src/openbiliclaw/recommendation/engine.py tests/test_recommendation_engine.py
git commit -m "feat: add recommendation reshuffle path"
```

### Task 3: API 新增 reshuffle 接口

**Files:**
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/api/models.py`
- Test: `tests/test_api_app.py`

**Step 1: Write the failing test**
- 增加 `/api/recommendations/reshuffle` 测试
- 验证未初始化时给出明确响应，已初始化时返回成功并带推荐数量

**Step 2: Run test to verify it fails**
Run: `PYTHONPATH=src .venv/bin/pytest tests/test_api_app.py -q`

**Step 3: Write minimal implementation**
- 新增 API schema
- 新增路由，对接 `RecommendationEngine.reshuffle_recommendations()`

**Step 4: Run test to verify it passes**
Run: `PYTHONPATH=src .venv/bin/pytest tests/test_api_app.py -q`

**Step 5: Commit**
```bash
git add src/openbiliclaw/api/app.py src/openbiliclaw/api/models.py tests/test_api_app.py
git commit -m "feat: expose recommendation reshuffle api"
```

### Task 4: popup 接“换一批”并即刻更新

**Files:**
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-api.js`
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup-helpers.js`
- Test: `extension/tests/popup-api.test.ts`
- Test: `extension/tests/popup-helpers.test.ts`

**Step 1: Write the failing test**
- 改写 popup API 测试，断言前端调用的是 `/api/recommendations/reshuffle`
- 增加 helper 断言，验证 fallback 展示 `relevance_reason`

**Step 2: Run test to verify it fails**
Run: `cd extension && npm test -- --runInBand`

**Step 3: Write minimal implementation**
- 按钮改为“换一批”
- 调用新 API
- 成功后立刻重拉推荐列表
- 保留 `refresh` 作为后台补货机制，不再绑定这个按钮

**Step 4: Run test to verify it passes**
Run: `cd extension && npm test -- --runInBand`

**Step 5: Commit**
```bash
git add extension/popup/popup.js extension/popup/popup-api.js extension/popup/popup.html extension/popup/popup-helpers.js extension/tests/popup-api.test.ts extension/tests/popup-helpers.test.ts
git commit -m "feat: reshuffle popup recommendations from pool"
```

### Task 5: 文档与全量验证

**Files:**
- Modify: `docs/modules/extension.md`
- Modify: `docs/modules/cli.md` (if wording needs runtime distinction)
- Modify: `docs/changelog.md`
- Modify: `docs/v0.1-todolist.md`

**Step 1: Update docs**
- 说明 `refresh` 与 `reshuffle` 的职责差异
- 更新 popup 交互说明

**Step 2: Run full verification**
Run:
```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/ tests/
PYTHONPATH=src .venv/bin/python -m mypy src/
PYTHONPATH=src .venv/bin/pytest -q
cd extension && npm test -- --runInBand
cd extension && npm run typecheck
cd extension && npm run build
```

**Step 3: Commit**
```bash
git add docs/modules/extension.md docs/changelog.md docs/v0.1-todolist.md
git commit -m "docs: update reshuffle recommendation flow"
```
