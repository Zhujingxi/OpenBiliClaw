# Init Embedding Auto-Pull Port Spec — 初始化缺模型不再死路,popup 显示拉取进度

**Created:** 2026-07-10
**Scope:** 从遗留分支 `feat/init-embedding-llm-sync`(提交 `db726daa`,分叉点 `b2f00780`,2026-07-07)移植仍有效的两块能力到当前 main:(1) guided init 命中 `embedding_not_ready` 且诊断为可拉取修复时自动触发 embedding 模型拉取;(2) 插件 popup 的 init checklist 渲染拉取进度条与修复按钮。涉及 `src/openbiliclaw/api/app.py`、`src/openbiliclaw/runtime/embedding_progress.py`、`extension/popup/`、对应测试与文档。
**Out of scope:** 原分支的 init LLM 失败分类(`_classify_init_llm_failure` 及 popup `llm_*` 错误码文案)——已被 main `bc2dc983` 的 `llm/base.py:describe_llm_failure` 取代,**不移植**;任何版本号变更(pyproject / manifest / package.json,发版走 release runbook);`/setup/` 页(已有进度条,不动);移动端 Web(popup 与 setup 覆盖后 CLI 走日志,四表面契约在 changelog 中声明豁免理由)。

## Goal

当前失败:embedding 为初始化硬前置且模型缺失时,`POST /api/init` 返回**裸 409**(`app.py:2413-2415`,无 detail、不自愈),用户死在「向量模型还没就绪」,须自己发现修复按钮(field report 2026-07-07:bge-m3 未 pull → init 卡死)。popup 的 init checklist 完全忽略 `embedding_repair_*` / `ollama_phase` / `embedding_pull_status` 字段,init 期间既无进度也无修复入口。

目标结果(验证命令见各 Phase 验收):

1. 诊断为 `model_missing` / `model_broken` 时,`POST /api/init` 的 409 响应携带实时进度 detail,且后台拉取已自动启动;其余诊断(not_running / 路径编码 / 磁盘满等)行为不变,仍指向手动修复。
2. popup init checklist 的向量模型行在拉取期间渲染进度条(百分比 + phase 文案),`model_missing` / `model_broken` 时提供修复按钮,点击后轮询 init-status 实时刷新。
3. `pytest tests/test_api_app.py`、`cd extension && npm test`、`ruff check`、`mypy src/` 全绿。

**为什么在 v0.3.160 已捆绑模型后仍需要**:v0.3.160 只覆盖 Docker 镜像与桌面 with-embedding 变体。git 安装(`pip install -e`)、桌面 lean 变体没有捆绑;模型损坏(`model_broken`)在所有安装形态都可能发生。窄化但真实。

## Design invariants (MUST hold in every phase)

1. **不引入竞争分类器:** 移植结果里不得出现 `_classify_init_llm_failure` 或 popup 的 `llm_rate_limited` / `llm_auth_failed` / `llm_unavailable` 错误码映射。验证:`git grep -n '_classify_init_llm_failure\|llm_rate_limited' -- src/ extension/` 在分支 tip 上零命中。LLM 失败继续走 main 现行的 `internal_error` + `describe_llm_failure` detail 路径(`app.py:519-521`)。
2. **自动拉取边界:** `_maybe_autostart_embedding_pull` 仅在 embedding provider 为 ollama 且 `diagnose_ollama_embedding` 返回 `DIAG_MODEL_MISSING` / `DIAG_MODEL_BROKEN` 时启动拉取;其他诊断码一律返回 False 不动作。验证:单测覆盖 not_running / 非 ollama provider / 已在拉取三条不触发路径。
3. **单飞复用,不新建状态:** 自动拉取必须复用现有 `_embedding_repair_lock` / `_embedding_repair_state` / `_run_embedding_repair` / `embedding_progress.mark_pull_running`(main 均已存在,`app.py` grep 计数 2/10/2/…),不得新增并行的状态容器或第二把锁。验证:并发触发单测断言只有一次拉取。
4. **永不阻断、永不抛出:** 自动拉取失败(诊断异常、任务启动异常)不得改变 409 主路径的语义,函数吞异常返回 False。验证:注入诊断异常的单测,409 响应仍返回且 detail 为手动修复引导文案。
5. **零版本号漂移:** 本 PR 的 `pyproject.toml`、`extension/manifest.json`、`extension/package.json`、`package-lock.json`、`src/openbiliclaw/__init__.py` 与 origin/main 逐字节一致。验证:`git diff origin/main -- <这五个文件>` 为空。
6. **popup 只读现有字段:** popup 进度视图仅消费 `/api/init-status` 已下发的 `embedding_repair_running/completed/total`、`ollama_phase`、`embedding_pull_status`、`embedding_check`(v0.3.157+ 已存在),不新增 API 字段或端点。验证:后端 diff 不含 init-status 响应结构变更。

## Current diagnosis

### D1. init 命中缺模型是死路(后端)

`origin/main:src/openbiliclaw/api/app.py:2413-2415`:`embedding_not_ready` 分支 `reset_to_idle` 后直接返回无 detail 的 409。对比 `bilibili_not_logged_in` 分支(`:2400-2408`)带 detail。基建已齐:`_embedding_repair_lock` / `_embedding_repair_state`(`:2430` 起)、`_run_embedding_repair`(`:2497`)、`_repair_progress_detail`(`:2443`)、`runtime/embedding_progress.py` 的 `mark_pull_running`(`:57`)/`snapshot`(`:103`)、`llm/ollama_diagnostics` 的 `DIAG_MODEL_MISSING`/`DIAG_MODEL_BROKEN`。缺的只是 init 路径上的自动触发器。确认事实,非假设:`git grep '_maybe_autostart_embedding_pull' origin/main` 零命中。

### D2. popup init checklist 对拉取进度失明(前端)

`origin/main:extension/popup/popup-init-control.js`(268 行)无任何 `embedding_repair_*` 消费;popup 的进度渲染只存在于**修复流程**(`popup.js:7157` 起,v0.3.155 语义修复横幅路径),init checklist 不渲染。`/setup/` 页已有进度条(v0.3.157),两端不对齐。遗留分支的增量:`embeddingPullProgressView`(纯函数,`{active, pct, label}`,pct 运行中钳 1–99)、`embeddingRepairAction`(按 `embedding_check` 选按钮)、checklist 行渲染 + `_handleChecklistEmbeddingRepair` 轮询驱动、popup.html 内联 CSS(`init-embed-pull*` / `init-repair-btn`)。

### D3. 遗留分支一半内容已被 main 取代(须丢弃)

分支 `db726daa` 的 `_classify_init_llm_failure`(app.py +55 行)与 main 同日下午的 `bc2dc983`(`describe_llm_failure` @ `llm/base.py:99`,`app.py:519-521` / `cli.py:6222` 两处消费)解决同一问题。main 方案层次更对(LLM 层翻译、detail 直达前端、popup 零映射表)。分支的 `TestInitLlmFailureClassification`(test_api_app.py)、init-control.test.ts 的 "classified LLM failure reasons" 用例、popup/setup 的 `llm_*` 文案、docs/modules/init.md 中相关段落全部随之丢弃。**移植方式必须是对照 diff 手工重放,不得 cherry-pick / merge `db726daa`**(popup.js 在分叉点后经 8 个提交重构,机械合并必污染)。

### D4. 分支还带一个测试隔离工具(顺带移植)

`runtime/embedding_progress.py` 是进程级单例;分支新增 `reset()`(+25 行)供测试清理 `running` 泄漏。main 无此函数,移植后端测试需要它。低风险纯增量。

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 1 | 后端自动拉取 + 409 detail + `embedding_progress.reset()` | **MUST** | 消灭 init 死路;所有前端进度显示的数据源 |
| 2 | popup checklist 进度条 + 修复按钮 | RECOMMENDED | 依赖 Phase 1 的 detail 语义;独立可跳(用户仍可靠 setup 页) |
| 3 | changelog + docs/modules/init.md | **MUST** | CLAUDE.md 文档要求,合并门禁 |

依赖:Phase 2 依赖 Phase 1(409 detail 文案被前端测试断言);Phase 3 依赖 1、2 定稿。全部一个 Wave(单 PR),Phase 1 可独立 ship,Phase 2 可安全停在"未做"。

## Phase designs

### Phase 1 — 后端自动拉取

接口:`_maybe_autostart_embedding_pull() -> bool`(create_app 闭包内,签名与分支版一致);`POST /api/init` 的 `embedding_not_ready` 409 增加 detail 字段(拉取中 → `_repair_progress_detail()`;未拉取 → 固定手动引导文案);硬前置分支之外增加软 embedding 自愈调用(`with suppress(Exception)`)。`embedding_progress.reset() -> None` 模块级函数。算法、锁序、状态字段逐字对照 `git diff b2f00780 db726daa -- src/openbiliclaw/api/app.py`(hunk @2432 / @2553)与 `runtime/embedding_progress.py` hunk。错误行为见不变量 4。

测试(`tests/test_api_app.py`,对照分支版重放并适配 main 现状):缺模型时 409 带进度 detail 且拉取任务启动;`model_broken` 同;`not_running` 不触发;非 ollama provider 不触发;已有拉取在跑时返回 True 不重复启动;诊断抛异常时 409 正常返回手动文案。每个测试 teardown 调 `embedding_progress.reset()`。

验收门:`.venv/bin/python -m pytest tests/test_api_app.py -k "autostart or autopull or embedding" -q` 全绿(新增 ≥6 用例);`.venv/bin/python -m pytest tests/test_api_app.py -q` 无回归;`ruff check src/ tests/`、`mypy src/` 零错误。

### Phase 2 — popup 进度视图

接口:`popup-init-control.js` 导出 `embeddingPullProgressView(prereq) -> {active, pct, label}` 与 `embeddingRepairAction(prereq) -> {show, label} | null`(纯函数,便于 node --test);checklist 渲染消费两者;`popup.js` 增加 `_handleChecklistEmbeddingRepair`(点击 → `startEmbeddingRepair()` → 轮询 init-status 重渲染,复用现有 `startEmbeddingRepair` API 封装 `popup-api.js:231`);popup.html 增量 CSS。对照 `git diff b2f00780 db726daa -- extension/popup/ extension/tests/` 重放,**剔除** `llm_*` 文案映射段。

测试(`extension/tests/init-control.test.ts`):移植分支的 3 条(pull 进度视图 / repair 按钮选择 / checklist 行组合),丢弃 "classified LLM failure reasons" 条。

验收门:`cd extension && npm test` 全绿且新增 3 用例;`npm run typecheck` 通过;`git diff origin/main -- extension/manifest.json extension/package.json extension/package-lock.json` 为空。

### Phase 3 — 文档

`docs/changelog.md` 当前版本块(v0.3.161)下加一条 bullet:init 缺模型自动拉取 + popup 进度对齐,注明源自遗留分支 `db726daa`、LLM 分类部分因 `bc2dc983` 取代而未移植、CLI 面不适用(init 由 Web/popup 驱动)。`docs/modules/init.md`:更新 `/api/init` 行与 `_init_wrapper` 段 —— 只描述自动拉取语义,版本标注用「v0.3.162+」;**不得**引入分支版 init.md 里的 `_classify_init_llm_failure` 叙述。

验收门:两文件变更与代码一致;pre-merge checklist(CLAUDE.md)逐项过。

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Phase 1 | git 安装 / desktop lean / 模型损坏场景下,init 缺模型从「死路 + 找按钮」变为「自动拉取 + 409 detail 带百分比」;拉完重试即通 |
| Phase 2 | popup 与 /setup/ 进度显示对齐,消除「popup 端 init 面板无进度无按钮」的表面差 |

## Documentation obligations

- `docs/changelog.md` — v0.3.161 块下新 bullet(Phase 3)
- `docs/modules/init.md` — `/api/init`、`/api/embedding/repair` 行与 `_init_wrapper` 段(Phase 3)
- 架构图 / README / CLI / config 文档 — 不触发(无跨模块接线变化、无新模块、无 CLI/config 变更)
- 四表面契约声明 — changelog bullet 中注明:popup + desktop-web(/setup/ 已有)覆盖,CLI 面 init 进度走既有日志输出,移动 Web 不含 init 面板,豁免
