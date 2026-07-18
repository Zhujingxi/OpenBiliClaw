# M91 Feedback Processing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 补平 `9.1 反馈处理`，统一打通 CLI、后端 API 和插件 popup 的反馈入口，并将反馈同步写入推荐记录与事件层。

**Architecture:** 继续复用现有 `recommendations` 表的当前反馈状态模型，不新增反馈历史表。新增 `POST /api/feedback`，并让 CLI 与 popup 都走统一的反馈语义和校验规则。

**Tech Stack:** FastAPI, Typer, SQLite, Chrome Extension MV3, vanilla JavaScript, Node test runner

---

### Task 1: Add failing Python tests for comment feedback and feedback API

**Files:**
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_recommendation_engine.py`

**Step 1: Write failing API tests**

新增测试覆盖：

- `POST /api/feedback` 成功提交 `like`
- `POST /api/feedback` 提交 `comment` 且缺少 `note` 时失败
- `POST /api/feedback` recommendation 不存在时失败

**Step 2: Write failing CLI test**

新增：

- `openbiliclaw feedback 7 comment --note "讲得还不够深"` 成功

**Step 3: Write failing engine test**

新增：

- `record_feedback(..., feedback_type="comment", note="...")` 能正常落库

**Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_api_app.py tests/test_cli.py tests/test_recommendation_engine.py -q
```

Expected: FAIL because API route and `comment` behavior still不完整。

**Step 5: Commit**

```bash
git add tests/test_api_app.py tests/test_cli.py tests/test_recommendation_engine.py
git commit -m "test: cover feedback api and comment flow"
```

### Task 2: Implement unified feedback API and CLI comment support

**Files:**
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/recommendation/engine.py`
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Add API schemas**

在 `models.py` 中新增：

- `FeedbackIn`
- `FeedbackResponse`

字段：

- `recommendation_id: int`
- `feedback_type: str`
- `note: str = ""`

**Step 2: Implement `/api/feedback`**

在 `app.py` 中新增 `POST /api/feedback`：

- 校验 `comment` 必须带 `note`
- 校验 recommendation 存在
- 调 `database.update_recommendation_feedback(...)`
- 追加一条 `feedback` 事件

**Step 3: Expand CLI feedback**

在 `cli.py` 中：

- 允许 `comment`
- 对 `comment` 无 `--note` 的情况返回明确错误
- 保持现有 `like/dislike` 行为不变

**Step 4: Keep engine interface consistent**

`RecommendationEngine.record_feedback()` 保持轻量，继续复用数据库写入，不把 CLI/API 分叉逻辑塞进去。

**Step 5: Run focused tests**

Run:

```bash
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest tests/test_api_app.py tests/test_cli.py tests/test_recommendation_engine.py -q
```

Expected: PASS

**Step 6: Commit**

```bash
git add src/openbiliclaw/api/models.py src/openbiliclaw/api/app.py src/openbiliclaw/recommendation/engine.py src/openbiliclaw/cli.py tests/test_api_app.py tests/test_cli.py tests/test_recommendation_engine.py
git commit -m "feat: add unified feedback api"
```

### Task 3: Add popup feedback helper tests

**Files:**
- Modify: `extension/tests/popup-helpers.test.ts`
- Modify: `extension/popup/popup-helpers.js`

**Step 1: Write failing tests**

新增测试覆盖：

- 构造 `like` / `dislike` feedback payload
- `comment` 缺 note 时返回无效
- `comment` 带 note 时 payload 正确

**Step 2: Run popup helper tests**

Run:

```bash
node --test --experimental-strip-types extension/tests/popup-helpers.test.ts
```

Expected: FAIL

**Step 3: Implement helper functions**

在 `popup-helpers.js` 中新增：

- `buildFeedbackPayload(...)`
- `validateCommentInput(...)`

**Step 4: Re-run helper tests**

Run:

```bash
node --test --experimental-strip-types extension/tests/popup-helpers.test.ts
```

Expected: PASS

**Step 5: Commit**

```bash
git add extension/popup/popup-helpers.js extension/tests/popup-helpers.test.ts
git commit -m "test: cover popup feedback helpers"
```

### Task 4: Wire popup feedback buttons and inline comment form

**Files:**
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-helpers.js`

**Step 1: Add inline comment UI**

在 popup 卡片中为每条推荐增加：

- `写一句` 按钮
- 隐藏的 comment 输入区
- 发送按钮

**Step 2: Implement feedback requests**

在 `popup.js` 中：

- 新增 `submitFeedback(payload)`，POST 到 `/api/feedback`
- `喜欢` / `不喜欢` 按钮触发真实请求
- `写一句` 展开 comment 区
- `发送` 提交 `feedback_type="comment"`

**Step 3: Add user-visible result hints**

提交成功：

- 显示“已记录你的反馈”

提交失败：

- 显示“反馈失败，请确认本地后端已启动”

**Step 4: Run extension checks**

Run:

```bash
cd extension
npm test
npm run typecheck
npm run build
```

Expected: PASS

**Step 5: Commit**

```bash
git add extension/popup/popup.html extension/popup/popup.js extension/popup/popup-helpers.js
git commit -m "feat: submit popup recommendation feedback"
```

### Task 5: Update docs for 9.1 completion

**Files:**
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/modules/recommendation.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`

**Step 1: Update task status**

在 `docs/v0.1-todolist.md` 中把 `9.1` 的前三项标记为完成。

**Step 2: Update module docs**

- `recommendation.md`：补反馈 API / comment 支持说明
- `cli.md`：更新 `feedback` 命令支持 `comment`
- `extension.md`：更新 popup 反馈按钮已接通

**Step 3: Update changelog**

在 `docs/changelog.md` 下追加 `9.1` 记录。

**Step 4: Review docs diff**

Run:

```bash
git diff -- docs/v0.1-todolist.md docs/modules/recommendation.md docs/modules/cli.md docs/modules/extension.md docs/changelog.md
```

Expected: only `9.1`-related changes.

**Step 5: Commit**

```bash
git add docs/v0.1-todolist.md docs/modules/recommendation.md docs/modules/cli.md docs/modules/extension.md docs/changelog.md
git commit -m "docs: update feedback workflow docs"
```

### Task 6: Run full verification

**Files:**
- Verify: `src/openbiliclaw/api/app.py`
- Verify: `src/openbiliclaw/api/models.py`
- Verify: `src/openbiliclaw/cli.py`
- Verify: `extension/popup/popup.js`
- Verify: `extension/popup/popup-helpers.js`

**Step 1: Run Python verification**

Run:

```bash
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m ruff check src/ tests/
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/
PYTHONPATH=src PIP_CONFIG_FILE=/dev/null /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m pytest -q
```

Expected:

- Ruff clean
- mypy clean
- pytest all pass

**Step 2: Run extension verification**

Run:

```bash
cd extension
npm test
npm run typecheck
npm run build
```

Expected: PASS

**Step 3: Manual validation checklist**

记录真实联调步骤：

- `openbiliclaw start`
- 打开 popup
- 点击 `喜欢`
- 点击 `不喜欢`
- 提交一条 comment
- 确认后端 `/api/feedback` 成功
- 确认 SQLite `recommendations` 和 `events` 有对应更新
