# Init Embedding Auto-Pull Port — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-10-init-embedding-autopull-port-spec.md`](./2026-07-10-init-embedding-autopull-port-spec.md)
> **Status:** rev2(已按 codex review 第一轮 9 条意见修订),pending codex re-review
> **Execution order:** Task 0 → Task 1 → Task 2 → Task 3(严格串行;Task 2 断言 Task 1 的 detail 文案)
> **Tech:** Python 3.14(**必须用 `.venv/bin/python`,裸 `python`/`python3` 无依赖无 pytest**);测试 `.venv/bin/python -m pytest <file> -q`;lint `.venv/bin/ruff check src/ tests/` + `.venv/bin/ruff format --check src/ tests/`;类型 `.venv/bin/mypy src/`;扩展 `cd extension && npm test && npm run typecheck`

**工作环境:** 在 worktree `/Users/white/workspace/OpenBiliClaw/.worktrees/init-embedding-llm-sync` 中执行。分支 `feat/init-embedding-autopull-port` **已存在**(基于 origin/main = `1e55d9d7`,venv 与 node_modules 已装好);开工前 `git -C <worktree> status` 确认在该分支且干净,若分支落后 origin/main 则 fast-forward,**不要**再执行 `checkout -b`。遗留分支 `feat/init-embedding-llm-sync`(`db726daa`)只作 diff 参照,**保持原样不动、不 merge、不 cherry-pick**。参照命令:`git diff b2f00780 db726daa -- <path>`(以符号/上下文定位插入点,diff 的 hunk 行号在当前 main 上不可靠)。

**Invariants that MUST hold — re-read before each task:**

- 不得引入 `_classify_init_llm_failure` 或 popup/setup 的 `llm_rate_limited` / `llm_auth_failed` / `llm_unavailable` 错误码映射(结构已被 main `describe_llm_failure` 取代;语义缺口在 Task 0 于 LLM 层补齐)。
- 自动拉取四重边界:provider=ollama、诊断为 `DIAG_MODEL_MISSING`/`DIAG_MODEL_BROKEN`、端点过 `is_loopback`(`runtime/ollama_supervisor.py:79`,含 `127.0.0.1:11435`)、磁盘守卫 `ollama_embedding_disk_space_error()` 为空;任一不满足返回 False。非 loopback(远端 / Docker `ollama` 主机名)绝不自动拉,保持 Docker seeder 的显式拉取策略不被旁路。
- 复用 `_embedding_repair_lock` / `_embedding_repair_state` / `_run_embedding_repair`,不新建锁或状态容器。
- `_maybe_autostart_embedding_pull` 永不抛出;任务调度失败必须回滚 running 状态并 `coro.close()`,不留僵尸 running。
- `pyproject.toml`、`extension/manifest.json`、`extension/package.json`、`extension/package-lock.json`、`src/openbiliclaw/__init__.py` 与 origin/main 逐字节一致(零版本号漂移)。
- popup 只消费 init-status 既有字段,后端不新增 API 字段。

### Task 0: LLM 层 — `describe_llm_failure` 补 auth / quota 桶

**Files:** 改 `src/openbiliclaw/llm/base.py`;测 `tests/test_llm_service.py`。

**Interfaces:** Consumes: 既有异常族(`LLMRateLimitError` 等)与消息字面。Produces: auth 桶(`authentication`/`unauthorized`/`invalid api key`/`401` → 鉴权失败中文文案,特异性排 moderation 之后、rate limit 之前);rate_limited 判定扩展纳入 `insufficient_quota`/`insufficient quota`/`quota`/`exhausted`/`429`,文案改「额度用尽或被限流」。签名与调用方不变。字面清单对照 `git show db726daa:src/openbiliclaw/api/app.py` 中 `_classify_init_llm_failure`。

**Steps:**

- [ ] 在 `tests/test_llm_service.py` 现有 describe_llm_failure 用例旁写失败测试:401/invalid-api-key 链、insufficient_quota 链、429 链各返回对应文案。
- [ ] 跑 `.venv/bin/python -m pytest tests/test_llm_service.py -q` 确认新用例 FAIL。
- [ ] 在 `llm/base.py` 加 auth 桶、扩 quota 字面,最小实现。
- [ ] 重跑确认 PASS 且既有五桶用例零回归。
- [ ] `.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/ && .venv/bin/mypy src/`。

**Acceptance:**

- 数值门:新增 ≥3 用例 PASS,`tests/test_llm_service.py` 全量零回归,ruff/mypy 零错误。
- 复现:`.venv/bin/python -m pytest tests/test_llm_service.py -q` 输出记入 PR。

### Task 1: 后端 — init 自动拉取 + 409 detail + embedding_progress.reset()

**Files:** 改 `src/openbiliclaw/api/app.py`、`src/openbiliclaw/runtime/embedding_progress.py`;测 `tests/test_api_app.py`、`tests/test_embedding_progress.py`(不存在则新建)。

**Interfaces:** Consumes: `diagnose_ollama_embedding` / `DIAG_MODEL_MISSING` / `DIAG_MODEL_BROKEN` / `ollama_embedding_disk_space_error`(`llm/ollama_diagnostics.py:222`)、`runtime.ollama_supervisor.is_loopback`、`_embedding_repair_lock`、`_embedding_repair_state`、`_run_embedding_repair`、`_embedding_ollama_target`、`_repair_progress_detail`、`_fire_and_forget_tasks`、`embedding_progress.mark_pull_running/snapshot`。Produces: `_maybe_autostart_embedding_pull() -> bool`(create_app 闭包,分支版基线 + spec D5 三处修正:loopback 边界 / 磁盘守卫 / 调度失败回滚);`POST /api/init` 的 `embedding_not_ready` 409 带 detail;硬前置外的软自愈调用(`with suppress(Exception)`);`embedding_progress.reset() -> None`。

**Steps:**

- [ ] 对照 `git diff b2f00780 db726daa -- src/openbiliclaw/runtime/embedding_progress.py` 移植 `reset()`;在 `tests/test_embedding_progress.py` 写 reset 行为测试(置位后 reset 归零)。
- [ ] 在 `tests/test_api_app.py` 加 autouse yield fixture:每用例前后各调一次 `embedding_progress.reset()`,teardown 侧先等/取消未决拉取任务再 reset。
- [ ] 写失败测试:embedding 硬前置 + 诊断 `model_missing` + loopback 端点时 `POST /api/init` 返回 409、detail 非空、拉取已启动(mock `diagnose_ollama_embedding` 与 `_run_embedding_repair`)。
- [ ] 跑 `.venv/bin/python -m pytest tests/test_api_app.py -k autostart -q` 确认 FAIL。
- [ ] 移植 `_maybe_autostart_embedding_pull`(定位:`_run_embedding_repair` 定义之后、`start_embedding_repair` 端点之前;基线来自分支 diff 的同名函数 hunk)+ `start_guided_init` 的 `embedding_not_ready` 分支改造(409 加 detail)+ 软自愈调用;叠加三处修正:(a) `is_loopback(base_url)` False → return False;(b) 锁内诊断通过后、置 running 前,`ollama_embedding_disk_space_error(model)` 非空 → return False;(c) 调度包 try,失败回滚 `_embedding_repair_state`(running=False, error 记录)与 embedding_progress、`coro.close()`、return False。
- [ ] 重跑确认 PASS,补齐边界用例:`model_broken` 触发 / `not_running` 不触发 / 非 ollama provider 不触发 / 远端 URL 不触发 / `http://ollama:11434` 不触发 / `http://127.0.0.1:11435` 允许 / 磁盘不足不触发 / 已有拉取在跑返回 True 不重复 / 诊断抛异常 409 仍返回手动文案 / 调度失败状态回滚 / auto-pull 与手动 `/api/embedding/repair` 并发只有一次拉取(竞态须用 auto vs 手动组合,两个并发 `/api/init` 会被 InitCoordinator 拦截测不到修复锁)。
- [ ] `.venv/bin/python -m pytest tests/test_api_app.py tests/test_embedding_progress.py -q`,然后**全量** `.venv/bin/python -m pytest -q`。
- [ ] `.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/ && .venv/bin/mypy src/`。

**Acceptance:**

- 数值门:新增 ≥10 用例全 PASS;全量 pytest 零回归;ruff/mypy 零错误。
- 不变量门:`git grep -nE '_classify_init_llm_failure|llm_rate_limited|llm_auth_failed|llm_unavailable' -- src/` 零命中;`git diff origin/main -- pyproject.toml src/openbiliclaw/__init__.py` 为空。
- 复现:两条 pytest 命令输出记入 PR。

### Task 2: popup — init checklist 进度条 + 修复按钮

**Files:** 改 `extension/popup/popup-init-control.js`、`extension/popup/popup.js`、`extension/popup/popup.html`;测 `extension/tests/init-control.test.ts`。

**Interfaces:** Consumes: init-status `prerequisites` 的 `embedding_repair_running/completed/total`、`ollama_phase`、`embedding_pull_status`、`embedding_check`;`popup-api.js` 现有 `startEmbeddingRepair`(约 `:235`,以符号定位)。Produces: 导出纯函数 `embeddingPullProgressView(prereq) -> {active, pct, label}`、`embeddingRepairAction(prereq) -> {repairable, label}`(契约以分支源码为准);checklist 行渲染;`_handleChecklistEmbeddingRepair` 轮询;`init-embed-pull*` / `init-repair-btn` CSS。

**Steps:**

- [ ] 在 `extension/tests/init-control.test.ts` 移植分支的 3 条测试(对照 `git diff b2f00780 db726daa -- extension/tests/init-control.test.ts`,**丢弃** "classified LLM failure reasons" 条)。
- [ ] 跑 `cd extension && npm test` 确认新用例 FAIL(导出缺失)。
- [ ] 对照 `git diff b2f00780 db726daa -- extension/popup/` 移植视图函数、渲染接线、repair 轮询与 CSS;**剔除** `llm_*` 文案映射;main 的 popup.js 分叉后经 8 个提交重构,插入点按当前代码以符号手工定位(init checklist 渲染函数与 `startEmbeddingRepair` 消费处)。
- [ ] 重跑 `npm test` 确认 PASS,再 `npm run typecheck`。
- [ ] 验证零漂移:`git diff origin/main -- extension/manifest.json extension/package.json extension/package-lock.json` 为空。

**Acceptance:**

- 数值门:npm test 全绿且比 origin/main 多 3 个通过用例;typecheck 零错误。
- 不变量门:`git grep -nE 'llm_rate_limited|llm_auth_failed|llm_unavailable' -- extension/` 零命中。
- 复现:`cd extension && npm test 2>&1 | tail -5` 记入 PR。

### Task 3: 文档

**Files:** 改 `docs/changelog.md`、`docs/modules/init.md`、`docs/modules/runtime.md`、`docs/modules/extension.md`。

**Interfaces:** Consumes: Task 0/1/2 定稿行为。Produces: v0.3.161 块下 bullet(init 缺模型自动拉取含 loopback/磁盘边界 + popup 进度对齐 + describe_llm_failure 补 auth/quota;注明源自遗留分支 `db726daa`、LLM 分类结构因 `bc2dc983` 取代未移植、四表面契约豁免:CLI init 进度走日志、移动 Web 无 init 面板);`init.md` 的 `/api/init`、`/api/embedding/repair` 行与 `_init_wrapper` 段(版本标注 v0.3.162+,不含分类器叙述);`runtime.md` 的 `embedding_progress` 节补 `reset()`;`extension.md` 补 popup init checklist 进度/修复行为。

**Steps:**

- [ ] 写 changelog bullet(≤6 行,对照分支版 changelog 但剔除 LLM 分类段、版本号改说法)。
- [ ] 更新 `docs/modules/init.md` 两处表行 + `_init_wrapper` 段;`runtime.md` + `extension.md` 对应节。
- [ ] 自查:新增文档内容中 `git grep -nE '_classify_init_llm_failure|llm_rate_limited|llm_auth_failed|llm_unavailable' -- docs/modules/ docs/changelog.md` 零新增命中(历史条目除外)。

**Acceptance:**

- CLAUDE.md pre-merge checklist 逐项通过(架构图/CLI/config/installer/README 均不触发,在 PR 描述声明)。
- 复现:`git diff origin/main --stat -- docs/` 仅含上述四文件(spec/plan 对在 main 上单独提交,不在本分支)。

## Verification after merge

合并到 main 后,由监工(Claude)在本机做一次真实验收,**不动用户真实 bge-m3 存储与正式配置**:用一次性临时目录起隔离环境(临时 `OPENBILICLAW_*` 配置指向临时 db + 临时 Ollama store,或对 Ollama 端点用受控 mock:`/api/tags` 返回空模型列表、`/api/pull` 返回流式进度),`serve-api` 起后端,`curl -X POST 127.0.0.1:8420/api/init` 确认 409 detail 带进度文案且 `GET /api/embedding/repair` 报 running;popup(或直接断言 `/api/init-status` 的 `embedding_repair_*` 字段)确认进度可见。验证毕删除临时目录,用户环境零残留。观察责任人:本会话 Claude;时长:单轮验证。回滚触发条件:自动拉取在非 loopback 或非目标诊断下误启动,或 409 语义变化导致现有前端重试逻辑异常 —— revert 合并提交。

## Explicitly out of scope

- `_classify_init_llm_failure` 结构及 popup `llm_*` 错误码(语义缺口已由 Task 0 在 LLM 层补齐)
- 版本号 bump 与发版(release runbook 另行处理)
- `/setup/` 页与移动 Web 改动
- 遗留分支 `feat/init-embedding-llm-sync` 的删除(移植合并后由收尾步骤处理)
