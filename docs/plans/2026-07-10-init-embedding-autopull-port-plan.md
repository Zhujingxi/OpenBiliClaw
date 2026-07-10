# Init Embedding Auto-Pull Port — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-10-init-embedding-autopull-port-spec.md`](./2026-07-10-init-embedding-autopull-port-spec.md)
> **Status:** draft, pending codex review
> **Execution order:** Task 1 → Task 2 → Task 3(严格串行;Task 2 断言 Task 1 的 detail 文案)
> **Tech:** Python 3.14(**必须用 `.venv/bin/python`,裸 `python`/`python3` 无依赖无 pytest**);测试 `.venv/bin/python -m pytest tests/test_api_app.py -q`;lint `.venv/bin/ruff check src/ tests/` + `.venv/bin/ruff format --check src/ tests/`;类型 `.venv/bin/mypy src/`;扩展 `cd extension && npm test && npm run typecheck`

**工作环境:** 在 worktree `.worktrees/init-embedding-llm-sync` 中执行。先 `git fetch origin && git checkout -b feat/init-embedding-autopull-port origin/main`。遗留分支 `feat/init-embedding-llm-sync`(`db726daa`)只作 diff 参照,**保持原样不动、不 merge、不 cherry-pick**。参照命令:`git diff b2f00780 db726daa -- <path>`。

**Invariants that MUST hold — re-read before each task:**

- 不得引入 `_classify_init_llm_failure` 或 popup/setup 的 `llm_rate_limited` / `llm_auth_failed` / `llm_unavailable` 错误码映射(已被 main `describe_llm_failure` 取代)。
- 自动拉取仅在 provider=ollama 且诊断为 `DIAG_MODEL_MISSING` / `DIAG_MODEL_BROKEN` 时启动;复用 `_embedding_repair_lock` / `_embedding_repair_state` / `_run_embedding_repair`,不新建锁或状态容器。
- `_maybe_autostart_embedding_pull` 永不抛出;失败不改变 409 主路径语义。
- `pyproject.toml`、`extension/manifest.json`、`extension/package.json`、`extension/package-lock.json`、`src/openbiliclaw/__init__.py` 与 origin/main 逐字节一致(零版本号漂移)。
- popup 只消费 init-status 既有字段,后端不新增 API 字段。

### Task 1: 后端 — init 自动拉取 + 409 detail + embedding_progress.reset()

**Files:** 改 `src/openbiliclaw/api/app.py`、`src/openbiliclaw/runtime/embedding_progress.py`;测 `tests/test_api_app.py`。

**Interfaces:** Consumes: `diagnose_ollama_embedding` / `DIAG_MODEL_MISSING` / `DIAG_MODEL_BROKEN`(`llm/ollama_diagnostics`)、`_embedding_repair_lock`、`_embedding_repair_state`、`_run_embedding_repair`、`_embedding_ollama_target`、`_repair_progress_detail`、`_fire_and_forget_tasks`、`embedding_progress.mark_pull_running/snapshot`(main 均已存在)。Produces: `_maybe_autostart_embedding_pull() -> bool`(create_app 闭包);`POST /api/init` 的 `embedding_not_ready` 409 带 detail;硬前置外的软自愈调用;`embedding_progress.reset() -> None`。

**Steps:**

- [ ] 对照 `git diff b2f00780 db726daa -- src/openbiliclaw/runtime/embedding_progress.py` 移植 `reset()`(纯增量)。
- [ ] 在 `tests/test_api_app.py` 写失败测试:embedding 硬前置 + 诊断 `model_missing` 时 `POST /api/init` 返回 409、detail 非空、拉取已启动(mock `diagnose_ollama_embedding` 与 `_run_embedding_repair`/Ollama HTTP)。
- [ ] 跑 `.venv/bin/python -m pytest tests/test_api_app.py -k autostart -q` 确认 FAIL(缺失行为)。
- [ ] 对照 `git diff b2f00780 db726daa -- src/openbiliclaw/api/app.py` 的 hunk @2432(409 detail + 软自愈)与 @2553(`_maybe_autostart_embedding_pull`)移植到 main 当前对应位置(`app.py:2413` 一带与 `_run_embedding_repair` 之后);**跳过** hunk @518(分类器)与 @2338(crash handler 改动)。
- [ ] 重跑确认 PASS,补齐其余用例:`model_broken` 触发 / `not_running` 不触发 / 非 ollama provider 不触发 / 已在拉取返回 True 不重复启动 / 诊断抛异常时 409 仍返回手动引导 detail。每用例 teardown 调 `embedding_progress.reset()`。
- [ ] 跑 `.venv/bin/python -m pytest tests/test_api_app.py -q`、`.venv/bin/ruff check src/ tests/`、`.venv/bin/ruff format --check src/ tests/`、`.venv/bin/mypy src/`。

**Acceptance:**

- 数值门:新增 ≥6 个用例全 PASS;`tests/test_api_app.py` 全量零回归;ruff/mypy 零错误。
- 不变量门:`git grep -n '_classify_init_llm_failure' -- src/` 零命中;`git diff origin/main -- pyproject.toml src/openbiliclaw/__init__.py` 为空。
- 复现:`.venv/bin/python -m pytest tests/test_api_app.py -q` 输出记入 PR。

### Task 2: popup — init checklist 进度条 + 修复按钮

**Files:** 改 `extension/popup/popup-init-control.js`、`extension/popup/popup.js`、`extension/popup/popup.html`;测 `extension/tests/init-control.test.ts`。

**Interfaces:** Consumes: init-status `prerequisites` 的 `embedding_repair_running/completed/total`、`ollama_phase`、`embedding_pull_status`、`embedding_check`;`popup-api.js` 现有 `startEmbeddingRepair`。Produces: 导出纯函数 `embeddingPullProgressView(prereq)`、`embeddingRepairAction(prereq)`;checklist 行渲染;`_handleChecklistEmbeddingRepair` 轮询;`init-embed-pull*` / `init-repair-btn` CSS。

**Steps:**

- [ ] 在 `extension/tests/init-control.test.ts` 移植分支的 3 条测试(对照 `git diff b2f00780 db726daa -- extension/tests/init-control.test.ts`,**丢弃** "classified LLM failure reasons" 条)。
- [ ] 跑 `cd extension && npm test` 确认新用例 FAIL(导出缺失)。
- [ ] 对照 `git diff b2f00780 db726daa -- extension/popup/` 移植视图函数、渲染接线、repair 轮询与 CSS;**剔除** `llm_*` 文案映射;注意 main 的 popup.js 分叉后经 8 个提交重构,插入点须按当前代码手工定位(init checklist 渲染函数与现有 `startEmbeddingRepair` 消费处)。
- [ ] 重跑 `npm test` 确认 PASS,再跑 `npm run typecheck`。
- [ ] 验证零漂移:`git diff origin/main -- extension/manifest.json extension/package.json extension/package-lock.json` 为空。

**Acceptance:**

- 数值门:npm test 全绿且比 origin/main 多 3 个通过用例;typecheck 零错误。
- 不变量门:`git grep -n 'llm_rate_limited\|llm_auth_failed\|llm_unavailable' -- extension/` 零命中。
- 复现:`cd extension && npm test 2>&1 | tail -5` 记入 PR。

### Task 3: 文档

**Files:** 改 `docs/changelog.md`、`docs/modules/init.md`。

**Interfaces:** Consumes: Task 1/2 定稿行为。Produces: v0.3.161 块下 bullet(注明源自遗留分支 `db726daa`、LLM 分类因 `bc2dc983` 取代未移植、四表面契约豁免声明);`docs/modules/init.md` 的 `/api/init` 行、`/api/embedding/repair` 行、`_init_wrapper` 段更新(版本标注 v0.3.162+,不含分类器叙述)。

**Steps:**

- [ ] 写 changelog bullet(≤5 行,对照分支版 changelog 但剔除 LLM 分类段、版本号改说法)。
- [ ] 更新 `docs/modules/init.md` 两处表行 + `_init_wrapper` 段。
- [ ] 自查:`git grep -n 'classify\|llm_rate_limited' -- docs/modules/init.md docs/changelog.md` 新增内容零命中(历史条目除外)。

**Acceptance:**

- CLAUDE.md pre-merge checklist 逐项通过(架构图/CLI/config/installer/README 均不触发,在 PR 描述声明)。
- 复现:`git diff origin/main --stat -- docs/` 仅含上述两文件 + 本 spec/plan 对。

## Verification after merge

合并到 main 后,由监工(Claude)在本机做一次真实验收:临时把配置指向不存在的 embedding 模型名(或临时改名本地 bge-m3 目录),`openbiliclaw serve-api` 起后端,`curl -X POST 127.0.0.1:8420/api/init` 确认 409 detail 带进度文案且 `GET /api/embedding/repair` 报 running;popup 加载后 init 面板出现进度条。观察一轮后恢复配置。回滚触发条件:自动拉取在 `not_running` 等非目标诊断下误启动,或 409 语义变化导致现有前端重试逻辑异常 —— 直接 revert 合并提交。

## Explicitly out of scope

- `_classify_init_llm_failure` 及一切 LLM 失败分类(已被 `describe_llm_failure` 取代)
- 版本号 bump 与发版(release runbook 另行处理)
- `/setup/` 页与移动 Web 改动
- 遗留分支 `feat/init-embedding-llm-sync` 的删除(移植合并后由收尾步骤处理)
