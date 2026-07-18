# 增量式架构重构实施计划

**日期**: 2026-07-19 | **状态**: 待执行 | **输入**: 仓库清单 [`docs/plans/2026-07-19-repository-inventory.md`](2026-07-19-repository-inventory.md)（上游盘点任务产出）、当前基线实测

**路径约定**: 本文件中的 `$REPO_ROOT` 指 Git 仓库根目录（即本文件向上两级目录）。执行任务的 kanban workspace 指向上级目录，真正的 Git 根是 `$REPO_ROOT`（即 `<workspace>/main`）；所有后续实现任务必须在 `$REPO_ROOT` 内提交。

---

## 1. 背景与实测基线

### 1.1 仓库规模（来自父任务清单，2026-07-19）

- 总计 260,842 行 / 1,251 个文件：Python 187,748（500 文件，其中测试约 92K 行 / 254 文件 / 约 4,928 用例）、TypeScript 32,113（155 文件）、JavaScript+Genshi 19,261、HTML 10,166、其余为 CSS/YAML/Bash/TOML。
- 超大源文件（高价值重构目标）：
  - `storage/database.py` 11,860 行（单类 ~100+ 方法，原生 sqlite3，无 ORM）
  - `api/app.py` 11,371 行（单文件 ~130+ 端点的 FastAPI 应用）
  - `cli.py` 9,253 行（Typer 入口，含大量运行时工厂与展现代码）
  - `recommendation/engine.py` 3,317 / `runtime/refresh.py` 3,216 / `discovery/engine.py` 3,059 / `config.py` 2,801 / `soul/speculator.py` 1,976
- 前端三族四面：`web/desktop/`（桌面网页 SPA）、`web/js/`（移动网页 PWA）、`extension/`（Chrome/Firefox MV3 扩展，popup 为 14K 行手写 vanilla JS），加 CLI 构成**四个用户可见面**。CLAUDE.md 规则 5 明确要求用户可见功能按"四面契约"处理。
- 7 个平台生产者（bilibili / douyin / reddit / x / xhs / youtube / zhihu）共享相似生命周期，但各自复制了 `_ensure_ledger_table` 等样板。
- 重复模式 6 类：`_ensure_*_columns` 列迁移（database.py 第 5979–6333 行约 15 个近似方法）、生产者 ledger 表创建、`_truncate*` 三份实现、散落各处的 `DEFAULT_*` 常量、生产者生命周期、扩展端 `*-task-dispatcher.ts` 样板。
- 双配置路径：TOML 应用配置（`config.py`，25 个顶层节）与 `model_config/`（5,751 行，独立迁移/序列化/版本追踪子系统）。

### 1.2 质量基线（本任务实测，直接捕获退出码）

| 检查 | 实测结果 |
|------|----------|
| `ruff check src/ tests/` | exit 0，"All checks passed!" |
| `mypy src/` | exit 1：10 个错误，全部位于 `src/openbiliclaw/cli_models.py`（Typer/click 类型兼容问题） |
| `pytest -q` | exit 1：**5598 passed, 1 failed, 45 skipped**（147s）。唯一失败节点：`tests/test_aggregate_release_workflow.py::test_aggregate_release_helper_does_not_backfill_previous_channel_assets` |

以上基线是在 **HEAD `7e6c5c77` 的调和后代码树**上测得（当时工作区仅含本规划任务新增的文档改动）。父任务清单中"5,599 passed"为陈旧/不一致数据，以本次直接退出码实测为准。后续所有验收的表述必须是"**不新增失败/错误**"，而不是死板的绝对通过数。

**覆盖率未测**：当前没有可信的覆盖率数字；Phase 0 必须在基线 check-in 时首次记录（`pytest --cov=openbiliclaw`），本计划不暗示一个现有数值。

### 1.3 工作区前提风险（执行前必须遵守）

**历史背景**：规划初期曾观察到一批与本计划无关的未提交 model-config 前端改动；这批文件在规划任务最终校验时已不在本工作树的快照中。规划任务最终校验时的快照（提交前时点，仅作历史记录，提交后 HEAD 与 status 会自然变化）：分支 `main`，HEAD `7e6c5c77`，工作区仅含本规划任务自身的三份文档改动：

```
 M docs/changelog.md
?? docs/plans/2026-07-19-incremental-architecture-refactor-plan.md
?? docs/plans/2026-07-19-repository-inventory.md
```

**前提条件 P-0（通用化）**：任何实现 PR 必须从**专用分支/工作树**开始，启动时记录 HEAD 与 `git status` 输出；一旦出现与本 PR 无关的未提交改动，**立即停止**并在任务上报告。严禁 reset、stash、格式化、覆盖或吸收他人未提交文件。

---

## 2. 目标与非目标

### 2.1 目标

1. **内聚**: 按业务域拆散超大模块，使每个模块有单一、可命名的职责。
2. **显式依赖**: 用分层与端口（protocol）替换"随处 import、随处构造"的隐式依赖。
3. **兼容接缝**: 所有对外入口（HTTP 路由、CLI 命令、`create_app`、`openbiliclaw.cli:app`、Database 公共方法）在迁移期间保持不变。
4. **可评审的小步变更**: 每个 PR 只动一个架构接缝；任何阶段在绿灯状态下都可以安全停止，剩余阶段不构成 mega-PR 的理由。

### 2.2 非目标（明确排除）

- 全面重写 / 换语言 / 换存储引擎（保留 SQLite）。
- 前端框架迁移（不引入 React/Vue/Vite；popup 保持 vanilla）。
- HTTP API 重新设计、路由路径变更、WebSocket 消息格式变更。
- 配置优先级或双配置合并的行为变更（仅建只读适配层，见 Phase 7）。
- 大范围目录改名 / 包名变更。
- **即时实施批次（Must 档，见 §6.0）不授权任何有意的用户可见行为变更**。过程中发现的 bug 修复必须单独提交：独立 commit + 回归测试 + `docs/changelog.md` 条目，不得混在重构 PR 里。

### 2.3 有意的行为变更声明

即时实现批次**没有任何有意的应用/用户可见行为变更**。允许的内部结构变化仅限三类：模块归属调整、委托/门面引入、测试与 CI 强制项新增。以下任何一项都属于**行为变更**，必须单独提交并经评审 + changelog 条目，不得混入重构 PR：迁移触发时机、HTTP/CLI 输出、配置优先级、调度时机、前端交互。

**唯一有意的工程行为变更在 CI**：基线感知门禁将只容忍以结构化身份显式记录的既有 pytest/mypy 诊断，同时拒绝任何新增诊断；诊断输出缺失或不可解析仍判失败（fail closed）。这属于验证方式的变化，不改变应用运行时行为。

---

## 3. 依赖方向（务实分层）

不引入教科书式 Clean Architecture 重写，采用务实的五层模型：

```
入口 / UI 层        extension popup, web desktop, web mobile, setup wizard
    ↓
传输适配层          FastAPI routers (api/), Typer commands (commands/), 浏览器适配器
    ↓
应用用例层          application services（编排一个用户/系统动作）
    ↓
领域策略层          domain policies + 类型化契约（pydantic/dataclass/protocol）
    ↓
基础设施层          SQLite (storage/), HTTP/平台客户端 (bilibili/, youtube/, sources/), 调度器
```

**强制规则**（将由 Phase 0 的 import-linter 风格检查固化）：

1. 领域层与应用层**不得** import `fastapi`、`typer`、DOM API，也**不得** import 或接收 `sqlite3` 连接。SQL、连接、锁、row_factory 全部归 `storage` 所有；应用服务只依赖 repository / unit-of-work protocol。
2. Router 与 Command **不得**直接构造 storage / 平台客户端——只能接收由组合根注入的窄化应用服务/protocol 依赖。
3. storage 模块**不得** import api 或 CLI 代码。
4. 平台特定代码必须留在 producer/source 契约之后。
5. **禁止**新建垃圾场模块：`utils.py`、`helpers.py`、`common.py`、单一巨型 `services.py`。共享逻辑按语义归属命名（如 `text/truncate.py`、`schema/migrations.py`）。

### 3.1 层间契约（实现时逐条遵守）

| 层 | 契约 |
|----|------|
| 传输适配层（routers / commands） | 只做请求校验、参数翻译、响应序列化；把既有领域错误映射为**不变的** HTTP/CLI 错误形状。不含业务分支。 |
| 应用服务层 | 拥有用例编排与**事务边界**（何时 commit / rollback 由服务决定，不由 repository 决定）。 |
| Repository | **绝不调用 `commit()` / `rollback()`**；事务边界由应用服务 / unit-of-work 持有。 |
| 兼容门面（迁移期 `Database`） | 仅在为保留某个既有 public 方法的确切行为时，允许代表该 legacy 方法打开并终结一个 unit-of-work；每个方法的行为对照表随 PR 提交。 |
| 领域/应用模块 | **绝不接收**裸 sqlite3 连接；只依赖 repository protocol 或类型化数据结构。 |
| 基础设施适配器 | 实现窄 protocol；平台 SDK 响应对象（bilibili-api、twitter_cli 等原始类型）**不得**向上层泄漏，必须在适配器内转为领域类型。 |

---

## 4. 目标模块边界

保留所有公共入口点；以下是**目标形态**，按 Phase 逐步逼近，不要求一步到位。

### 4.1 API（Phase 1）

- `api/app.py` 保留为 `create_app(...)` 组合根：只含中间件注册、静态资源白名单、lifespan、router include。**禁止**在过渡期内同时注册新旧两份同名路由。
- 新增 `api/dependencies.py`：类型化依赖定义模块。试点阶段每个 router 使用**独立的窄化依赖 dataclass**；仅当多个 router 的构造模式重复出现时，才允许引入 `ApiServices` 广容器，且它只存在于组合根（`create_app`）内部。
  - **反服务定位器约束**：每个 router 模块接收的是**窄化依赖包或 protocol**（如 `SystemRouteDeps`、`ProfileRouteDeps`，或某个具体应用服务），而不是能摸到所有 database / registry / engine 的全局容器。Router 代码中禁止出现 `deps.services.<任意引擎>` 式的穿透访问。
- 新增 `api/routes/` 包。每个 router 模块暴露**工厂函数**而非全局 router：

  ```python
  def build_system_router(deps: SystemRouteDeps) -> APIRouter: ...
  ```

  `create_app()` 构造窄化依赖并 include 返回的 router；router **不得**接收全局 `ApiServices` 容器。
- 提取顺序按闭包依赖从小到大：
  - **试点 PR**：`/api/ping` + `/api/qr-info` —— 实测 `/api/ping` 完全无闭包状态；`/api/qr-info` 仅依赖 `_health_lan_ip()` 工具函数。试点**不引入 `ApiServices`**：`create_app()` 直接从现有函数构造窄依赖，目标形态为
    ```python
    @dataclass(frozen=True)
    class SystemRouteDeps:
        get_lan_ip: Callable[[], str | None]

    def build_system_router(deps: SystemRouteDeps) -> APIRouter: ...
    ```
  - 第二个 PR：`/api/health`（readiness，依赖 profile/embedding 状态）+ `/api/init-status`，使用窄化 `HealthRouteDeps`
  - 之后按族推进：`auth.py`（`/api/auth/*`、cookie 端点）→ `profile.py`（`/api/profile*`、`/api/events`）→ `recommendations.py`（`/api/recommendations*`、`/api/delight/*`）→ `saved.py`（`/api/saved/*`、`/api/watch-later`、`/api/favorites`）→ `chat.py`（`/api/chat*`、probes、`/api/feedback`、`/api/insights/*`）→ `model_config.py` → `initialization.py`（`/api/init*`、`/api/embedding/repair`）→ `runtime.py`（`/api/runtime-status`、`/api/runtime-stream` WS、`/api/update*`、`/api/notifications/*`）→ `sources/`（按平台拆：`bili.py`, `xhs.py`, `x.py`, `dy.py`, `reddit.py`, `zhihu.py`, `recipes.py`）——**最后提取**，因为 task-dispatch 端点与运行时耦合最深
- Pydantic schema 按端点族迁入 `api/schemas/`，旧符号在 `app.py` 顶部 re-export 一个兼容期。
- **必须保持**：路由路径、HTTP 方法、response_model、状态码、错误体形状、WebSocket 行为、中间件顺序、lifespan 顺序、静态资源白名单、路由注册顺序（FastAPI 按注册序匹配，含 path-param 路由）。

### 4.2 存储（Phase 2–3）

- `storage/database.py` 的 `Database` 类保留为**兼容门面**，全部公共方法签名不变，内部委托给提取出的 repository，至少维持一个发布周期。
- 新增：
  - `storage/connection.py` —— 连接/事务/锁的所有权（单例连接管理、`:memory:` 支持、row_factory 集中定义）
  - `storage/migrations.py` —— 数据驱动的 `ensure_columns(...)` 列迁移工具（2A，见 §5 决策矩阵 M-1）；schema 版本账（2B）仅在有真实需求时另行提案
  - `storage/repositories/` —— 按域拆 DAO：`events.py`, `content_cache.py`, `recommendations.py`, `discoveries.py`, `saved_sync.py`, `llm_usage.py`（与清单建议一致）
- **硬性约束**: repository 方法必须使用调用方传入的连接/锁，或显式 unit-of-work 对象；**不得**静默改变提交时机、事务边界、并发语义或 `:memory:` 行为。提取前必须先补事务/锁并发测试（Phase 3 前置）。

### 4.3 CLI（Phase 5）

- 入口保持 `openbiliclaw = "openbiliclaw.cli:app"` 不变。
- **命名约束**: 只要 `cli.py` 还存在，就**不得**创建 `openbiliclaw/cli/` 包（同名 module/package 冲突）。新代码放 `openbiliclaw/commands/`：
  - `commands/auth.py`, `commands/login.py`, `commands/browser.py`, `commands/autostart.py`, `commands/ext_key.py`（对应现有 5 个 `add_typer` 子组）
  - `commands/recommend.py`, `commands/profile.py`, `commands/start.py` 等主命令
  - `commands/formatting.py`（`_print_*` 系列展现助手）
- 运行时工厂（`_build_*` 系列，cli.py 第 504–821 行）迁入 `runtime/bootstrap.py` 或按域归位（如 `_build_bilibili_client` → `bilibili/` 工厂函数）。
- `cli.py` 最终只剩：Typer app 定义、callback、`add_typer` 注册、各命令的 thin wrapper。
- **必须保持**：命令名、选项别名、help 文本、退出码、stdout/stderr 分流、import 时装饰器注册顺序、shell completion。

### 4.4 配置（Phase 7）

- 同理，`config.py` 存在期间**不得**创建 `config/` 包。
- TOML 应用配置与 `model_config/` 动态模型配置视为两个独立限界上下文，**首期只建只读类型化门面**：`config_facade.py`（或 `runtime/settings.py`）显式记录优先级（env override > TOML > 默认）与各字段的所有权归属。
- 合并两个配置存储、变更优先级——**显式推迟**，需另行 RFC。

### 4.5 前端（Phase 6）

- `web/shared/` 只放**面中立（surface-neutral）**代码：纯状态与 API 契约逻辑必须 DOM-free；共享渲染辅助**可以**操作显式注入的 DOM 根/文档，但禁止全局 DOM 查询、`chrome.*`、面特定选择器与隐藏浏览器状态。若 model-config 的 state/render 拆分（`model-config-state.js` / `model-config-render.js`）落地，Phase 6 必须**检视并基于它推广**，而不是重复或替换它。
- DOM 渲染、浏览器 API（chrome.*, fetch 拦截等）按面放适配/控制器：`web/desktop/assets/js/controllers/`、`web/js/controllers/`、扩展侧维持 `extension/src/` 分层。
- 不引入框架。
- 任何 API 行为变更按四面契约处理：popup / desktop web / mobile web / CLI 全覆盖或在 PR 中显式声明排除项，共享逻辑必须落在后端。

### 4.6 扩展（与 Phase 6 并行，独立 PR 流）

- `extension/src/background/*-task-dispatcher.ts`：提取共享 dispatcher 基座（现有 base pattern 之上收敛剩余样板），保持消息协议不变。
- popup（14K 行 vanilla JS）：按视图拆模块文件，沿用 state/render 分离模板；不改消息协议与 UI 行为。

### 4.7 CI/CD（Phase 0 接线 / Phase 8 演进）

目标架构按"验证 / 发布"分离工作流所有权：

- `.github/workflows/ci.yml` —— 只拥有 PR/push 验证：后端 ruff/mypy/pytest、归一化质量基线对比、API/存储契约测试、扩展 typecheck/test/build、受影响打包冒烟。**不得**持有发布凭据或对外发布产物。
- 渠道专属发布工作流（`release-backend.yml` / `release-desktop.yml` / `release-docker.yml` / `release-extension.yml` / `build-installers.yml` / Chrome Web Store 两个）—— 只拥有本渠道的打包/签名/发布；只作用于已通过验证的同一不可变 commit/版本；不重复通用单测逻辑，只做渠道特定产物校验。
- `verify-release-completeness.yml` —— 汇聚（fan-in）发布门禁：版本一致性、期望资产/渠道齐全、commit 同一性。
- 安全/运维契约：最小权限 `permissions`；发布 secrets 用 environment 作用域；PR job 无 secrets；发布 job 加 concurrency 守卫；版本一致性继续用 `scripts/release.py --check`。
- 增量顺序：**Must** 只把新契约/质量检查接入现有 `ci.yml`，不重设计发布触发器；**后期**在重复被证实后才提取 `workflow_call` 复用，发布工作流逐个迁移。
- 回滚：工作流变更独立 revert；发布触发器、secret、产物布局与应用重构**绝不**混在一个 PR。

---

## 5. 自定义轮子的处置决策矩阵

| # | 机制 | 决策 | 理由与约束 |
|---|------|------|-----------|
| M-1 | `database.py` 中约 15 个 `_ensure_*_columns` 重复方法 + 内联迁移 | **分两步替换**：(2A) 数据驱动的 `ensure_columns(conn, table, columns)` 小工具（约几十行），保留现有**惰性调用点**、事务时机与兼容 wrapper 方法；(2B) 仅当出现真实需求（如跨版本数据变换）时才引入 schema 版本账 | 2A 不引入新框架、不改变迁移触发时机——**禁止**把惰性迁移挪到应用启动（那会改变启动延迟与锁行为）。表名/列名在插值进 SQL 前必须对照**静态声明白名单**校验。若未来做 2B，必须具备：原子迁移、半迁移状态恢复、迁移前备份（复用 `storage.maintenance.create_database_backup`）、显式回滚语义。 |
| M-2 | 7 个生产者重复的生命周期 / `_ensure_ledger_table` 样板 | **隔离**：`Producer` protocol + 可组合协作者（共享 ledger/lifecycle 帮助函数）；**暂不引入 `BaseProducer` 基类** | 至少完成 **2 个**生产者迁移并证明存在稳定公共算法之后，才允许提案是否提取基类。只提取真实交集；禁止把平台特定行为塞进共享层。 |
| M-3 | 三份 `_truncate*`（`negative_exemplars.py` / `preference_analyzer.py` / `x_normalize.py`） | **按语义归并**，而非大一统工具 | 字节截断 / 字符截断 / UI 展示截断语义不同；若三者语义确实一致则合并到 `text/truncate.py`，否则各归其主并改名消歧。先写特征测试再动。 |
| M-4 | 散落 6 处的 `DEFAULT_*` 常量 | **归并到语义所有者** | 属于配置域的回 `config.py` 默认值声明；属于平台域的留在平台模块；不为归并而新建 `constants.py` 垃圾场。 |
| M-5 | 双配置路径（TOML vs `model_config/`） | **现在隔离于适配层之后**，统一推迟 | 合并涉及序列化格式、迁移与用户数据，复杂度超出本批；Phase 7 只编码现状优先级。 |
| M-6 | 大型 vanilla 前端渲染器（popup 14K 行、各 view） | **渐进隔离状态与渲染**；框架替换推迟 | 以 `model-config-state/render` 拆分为模板；框架迁移在本计划外。 |
| M-7 | FastAPI / Typer / SQLite / APScheduler / esbuild | **保留** | 这些是稳定的第三方地基；替换它们不解决当前的耦合问题，只增加风险。 |
| M-8 | 手写依赖构造（`create_app` 闭包、cli `_build_*`） | 小型 dataclass 服务容器（`ApiServices` / 运行时容器），**仅供组合根使用** | **不引入 DI 框架**；现有手写构造的问题是无组织而非无框架。广容器只在组合根内部可见，下游单元一律接收窄化依赖包。 |
| M-9 | 扩展端 7 个 `*-task-dispatcher.ts` 样板 | **隔离**：收敛到共享基座 | 与 M-2 同原则：只提取已证实的交集。 |

---

## 6. 迁移顺序（按 PR 粒度）

每个阶段可在绿灯状态下安全停止；每个 PR 只动一个架构接缝。下列 Phase 0–8 是**长期路线图**，不等于即时实现切片的一次性交付范围。

### 6.0 执行优先级（交付切分）

- **Must（即时实现切片）**：
  1. 从专用分支/工作树开始，启动时记录 HEAD 与 `git status`；确认不存在与本切片无关的未提交改动（发现即停止并上报）。
  2. 落地 Phase 0 产物：路由契约清单（`app.routes` + OpenAPI）+ `/api/ping`/`/api/qr-info` 精确响应测试 + 归一化质量基线（pytest/mypy/覆盖率）+ DB 兼容 fixture + 新提取模块的窄架构棘轮。**即时切片不触碰 CLI，不含 CLI 契约冻结**。
  3. 用窄依赖 router 工厂提取 `/api/ping` + `/api/qr-info` 试点（不引入 `ApiServices`）。
  4. 实现迁移去重 2A（`ensure_columns` 小工具，保留全部惰性调用点）。
  5. 把新契约/质量检查接入现有 `ci.yml`；发布工作流零改动。
  6. 更新被触碰模块的 `docs/modules/*.md` 与 `docs/changelog.md`；受影响检查全绿。
- **Should（后续）**：
  - 第二个 API 端点族（`/api/health` + `/api/init-status`）。
  - repository 提取试点（仅在事务/锁测试先行之后）。
  - Producer protocol + 帮助函数，并完成 **2 个**生产者迁移。
  - 一个 CLI 子命令组的提取试点。
- **Could / 路线图后期**：其余端点族、全部 repository/生产者、CLI 全量分解、前端/配置/引擎阶段。

### Phase 0 — 契约冻结与安全网（前置，1–2 个 PR）
- 内容（确切产物路径）：
  - `tests/contracts/api-route-contract.json` —— 归一化路由契约清单，由**两部分合成**：(a) `app.routes` 的顺序元数据（注册序号、route 类型、path、methods、name、是否为 WebSocket / Mount），(b) OpenAPI 提供的 HTTP operation ID 与 request/response schema shape。**不得只依赖 OpenAPI**——它不含 WebSocket 路由、Mount/静态路由及部分注册顺序信息，而这些正是本计划承诺保持不变的对象。生成器保留顶层 `app.routes` 注册顺序，同时规范无序字段（如每路由的 method 集合排序）。
  - `tests/test_api_route_contract.py` —— 清单归一化对比测试。
  - `tests/contracts/quality-baseline.json` —— 归一化质量基线：pytest 失败/跳过按 **node ID** 键控（用 JUnit XML 或等价免依赖结构化输出）；mypy 诊断按 `{path, error_code, 归一化消息}` 键控（去行号、滤 `note:` 重载噪声）；首次覆盖率记录。
  - `scripts/check_quality_baseline.py` —— 基线对比脚本（§7.2 规则）。
  - `tests/test_storage_schema_migrations.py` —— 迁移幂等/行为测试。
  - `tests/fixtures/storage_schema.py` —— **程序化**构建 empty / legacy / current / partial schema 的 fixture 函数；**不得**提交二进制 SQLite 数据库。
  - **行为等价断言**：为 `/api/ping` 与 `/api/qr-info` 添加**精确 JSON 体与 content-type** 断言（仅 OpenAPI 快照不足以证明响应体等价）。
  - **窄架构棘轮**：`tests/test_architecture.py` 只对**新提取模块**强制 §3 依赖规则，并登记存量例外清单；不声称五条规则可立即全仓强制——全仓强制属 Phase 8。
- 前置：P-0 已遵守。
- 回滚：纯新增测试，revert 即可。
- 出口：CI 上契约测试绿，且能证明"契约 diff 为空"可作为后续 PR 的机械验证手段。

### Phase 1 — API 路由提取（每端点族 1 个 PR）
- 顺序：router 工厂骨架 + 窄依赖包 → `/api/ping` + `/api/qr-info` 试点 → `/api/health` + `/api/init-status` → 之后按 §4.1 的族顺序推进（auth → profile → recommendations → saved → chat → model_config → initialization → runtime → sources/*）。
- router **不得**接收完整 `ApiServices` 容器；只接收窄化依赖包。
- 每 PR 验证：`app.routes` 顺序元数据 + OpenAPI 的归一化路由契约 diff 为空 + `TestClient` 族测试 + `pytest tests/test_api_app*.py` 不新增失败 + 静态资源白名单 diff 为空。
- 回滚：单 PR revert。
- 出口：`app.py` 不再含任何端点函数体，只做 include。

### Phase 2 — 迁移去重 2A（1 个 PR）
- 内容：引入小型数据驱动 `ensure_columns(conn, table, columns)` 工具；**先盘点**约 15 个 `_ensure_*_columns` 方法并刻画其行为，**只转换行为等价的纯增量者**（现有实现为：先 `PRAGMA table_info` 检查缺失列，再对缺失列执行 `ALTER TABLE ... ADD COLUMN`）；含自定义逻辑的方法保留原样并在 PR 中登记为例外，不得硬塞进通用 helper。
- **硬性约束**：保留每一个现有惰性调用点；保留事务/锁时机；表名与列名在插值进 SQL 前对照静态声明白名单校验；本阶段**不创建** schema 版本表。
- 验证：空库 / 当前库 / 半迁移库 / 旧库四种 fixture + 连跑两次幂等。
- **2B（schema 版本账 / 数据变换迁移）显式推迟**：仅在出现真实需求时另行提案，届时必须具备原子迁移、半迁移恢复、迁移前备份（`storage.maintenance.create_database_backup`）与显式回滚语义。
- 回滚：revert 代码即可恢复旧实现。**注意**：2A 只是在同一惰性调用点把同一批既有的增量 DDL 调用集中起来——没有引入新的 schema 转换；已执行过的 ADD COLUMN 本来就是现状的一部分，revert 代码不会（也不需要）回退这些列。

### Phase 3 — 存储门面与 repository 拆分（每域 1 个 PR，约 6 个 PR）
- 顺序：先补事务/锁/并发测试（前置 PR）→ `events` → `content_cache` → `recommendations` → `discoveries` → `saved_sync` → `llm_usage`。
- `Database` 公共方法逐一改为委托；签名、提交时机、锁行为逐一对照。
- 验证：fixture 库上的行为等价测试 + 并发测试 + `pytest tests/test_storage*.py` 不新增失败。

### Phase 4 — 生产者生命周期（M-2）
- PR 1：从 7 个实现归纳真实交集，定义 `Producer` protocol + 可组合的共享 ledger/lifecycle 帮助函数；本阶段**不创建** `BaseProducer` 基类。
- PR 2：迁移 **2 个**代表性生产者（建议 reddit + youtube，外部依赖较轻），验证契约测试套件可复用。
- 只有在 **2 个**迁移证明存在稳定公共算法之后，才允许提案是否提取基类；平台特定行为始终留在共享层之外。
- 其余生产者在确认模式稳定后各 1 个小 PR。
- 验证：共享契约测试套件对每个已迁移实现运行。

### Phase 5 — CLI 分解（约 4–6 个 PR）
- **前置（CLI 契约冻结）**：先生成 CLI 契约产物——`tests/contracts/cli-command-tree.json`（全命令树：命令名、选项别名、help 文本、退出码）+ `tests/test_cli_contract.py`（归一化对比测试）。
- 顺序：`commands/` 包骨架 + formatting 提取 → 5 个子组迁移 → 主命令迁移 → `_build_*` 工厂归位 → `cli.py` 收敛为纯组合。
- 验证：CLI 契约清单 diff 为空 + `pytest tests/test_cli*.py` 不新增失败 + 实际跑 `openbiliclaw start|recommend|profile|config-show` 冒烟（文档中记录输出）。
- `cli_models.py` 的 10 个既有 mypy 错误**不属于本阶段或即时切片**，作为独立技术债单独跟踪（见 §7.3 与 §11）。

### Phase 6 — 前端边界（先确认相关 model-config 分支/工作的状态与归属，再启动）
- 纯状态/API 契约模块先行；每面各 1 个 controller/renderer PR；四面回归矩阵测试。
- 扩展 dispatcher 基座与 popup 视图拆分走独立 PR 流，`npm run typecheck && npm run test && npm run build` 必须全绿。

### Phase 7 — 配置接缝（1–2 个 PR）
- 只读门面 + 优先级文档化 + 字段所有权表。**不改变任何优先级行为**。

### Phase 8 — 剩余引擎与 CI 固化
- `recommendation/engine.py` / `discovery/engine.py` / `runtime/refresh.py` 按内部边界拆分（评分/排序/解释生成；策略调度/候选评估/管道编排；平台刷新/池管理/调度）。
- CI 将 Phase 0 的架构断言与契约测试设为必需检查。

---

## 7. 测试与 CI 策略

### 7.1 每类变更的必备测试

| 变更类型 | 必备验证 |
|----------|----------|
| API 路由提取 | 规范化 OpenAPI/路由契约 diff 为空；端点族 `TestClient` 测试；静态资源白名单 diff 为空 |
| CLI 迁移 | help 文本、退出码、stdout/stderr 分流、命令名契约测试；手测冒烟记录 |
| DB/迁移 | 空库/旧库/当前库/半迁移库四 fixture；幂等性（连跑两次）；事务/锁/并发测试先于 repository 提取 |
| 生产者 | 共享契约测试套件对每个实现运行 |
| 前端 | 纯状态模块单测 + 每面 DOM/控制器测试；`npm run typecheck/test/build` |
| 打包相邻 | 任何 import 路径或静态资源变更：git editable 安装 + `docker compose up -d --build` + PyInstaller 桌面冒烟（CLAUDE.md 规则 6：三种安装模式都验） |

### 7.2 质量门（每个 PR 都要过）

所有基线以**归一化诊断基线文件**为准（check-in 到仓库），不允许用绝对计数硬编码在 CI 里：

- **ruff**：`ruff check src/ tests/` 零违规（当前为零）。
- **pytest**：以 JUnit XML（或等价免依赖结构化输出）按 **node ID** 键控失败/跳过。唯一允许失败的节点是 `tests/test_aggregate_release_workflow.py::test_aggregate_release_helper_does_not_backfill_previous_channel_assets`；其他任何新增 failure/error 一律拒绝。已知失败若被修复而消失，允许 allowlist 相应移除。新增 skip 必须在 PR 中显式说明并更新基线文件，否则拒绝；既有 skip 消失不拒绝。
- **mypy**：以稳定格式运行（如 `--no-error-summary --show-error-codes --no-color-output`），基线按 `{path, error_code, 归一化消息}` 键控：忽略行号，过滤 `note:` 重载噪声与平台/环境差异行。移除已知诊断始终允许；新增任何诊断一律拒绝——即使总数仍 ≤10。不得用宽泛 `ignore_errors` 掩盖。
- **覆盖率**：当前**未测**，不假定已达任何阈值。Phase 0 首次用 `pytest --cov=openbiliclaw` 记录真实值入基线文件；此后 PR 不允许下降超过噪声阈值（±0.5%），长期目标 ≥70%。
- **扩展**：`npm run typecheck && npm run test && npm run build` 全绿。

### 7.3 CI 调整

- **即时切片（Must）**：只把 Phase 0 产物接入现有 `ci.yml`——路由契约对比、`/api/ping`+`/api/qr-info` 精确响应测试、质量基线对比（pytest/mypy/覆盖率）、存储迁移 fixture 测试、新提取模块的窄架构棘轮。不重设计发布触发器，不动任何发布工作流。
- **后续（Should / Phase 8）**：把 Must 阶段的窄架构棘轮扩展为仓库级/独立架构 job；mypy 从"基线文件对比"切换为全量阻断（待 `cli_models.py` 存量修复后）；契约测试与覆盖率门禁设为必需检查。
- **非零退出处理**：pytest/mypy 当前均 exit 1，普通 `set -e` 会在基线对比器运行前终止。因此相关 job 必须：先捕获结构化输出与原始 exit code（不得中途终止），再由 `scripts/check_quality_baseline.py` 作**最终裁决**——仅当全部诊断被 allowlist 或已移除时才通过。**收集错误、工具内部崩溃、输出产物缺失或输出不可解析，一律判失败**，绝不允许被当作 allowlisted 失败吞掉。

---

## 8. 回滚与兼容策略

1. **一个 PR 一个架构接缝**：任何阶段出问题，Git revert 单 PR 即可；纯提取优先 revert，不维护长期双实现。
2. **兼容门面保留期**：`Database` 委托方法、API schema re-export 至少保留一个发布周期；移除前必须全仓库消费者搜索 + 至少跨一个 release。
3. **FastAPI 路由**：新旧路由**绝不同时注册**（同路径双注册会产生不可预期的匹配）。
4. **DB 迁移回滚分档**：
   - **Phase 2A（即时切片）**：只是把同一批既有增量 DDL 在同一惰性调用点集中去重，没有引入新 schema 转换——代码 revert 即完整回滚。
   - **未来 Phase 2B（数据变换迁移，若有）**：流程必须是——停写 → 用 `storage.maintenance.create_database_backup` 做含 WAL 的冷备份 → 原子迁移 → 校验完整性与版本 → 失败则恢复备份。**不得**假设任意 SQLite schema 变更可仅靠 Git revert 逆转。
5. **特性开关**只用于真正有行为风险的变化；纯文件移动不配开关。
6. **打包兼容**：import 路径变更 PR 必须跑 PyInstaller 冒烟（hidden import 与静态资源白名单是高发坑）。

---

## 9. 风险清单（实施时逐条盯防）

| 风险 | 缓解 |
|------|------|
| FastAPI 闭包捕获依赖 + 路由注册顺序改变匹配行为 | Phase 0 契约清单机械验证；按注册序逐个迁移 |
| WebSocket / lifespan / 后台任务清理语义漂移 | `runtime.py` 族放后面迁；补 WS 集成测试 |
| SQLite 锁与事务语义在 repository 拆分中漂移 | 并发测试先行；repository 强制使用调用方连接 |
| Typer import 时装饰器副作用 | 保持 `cli.py` 的 import 序；契约测试锁定命令树 |
| config/组合根引发循环 import | 架构断言禁止 storage→api、domain→fastapi 方向 |
| PyInstaller hidden imports / 静态资源白名单遗漏 | 打包冒烟列入 PR checklist |
| 四个前端面行为漂移 | 四面契约规则 + 回归矩阵 |
| 工作区脏文件归属冲突 | P-0 前提；前端 Phase 前完成协调 |
| 误"清理"已知 release-workflow 失败测试 | 显式禁令；该测试行为需独立审查 |
| 在第二个实现出现前造抽象 | M-2/M-9 要求从 7 个实现归纳交集，禁止先验基类 |

---

## 10. 可度量验收标准

### 10.1 即时切片（Must，见 §6.0）

- [ ] 实现从专用分支/工作树开始：已记录 HEAD 与 `git status`；不存在与本切片无关的未提交文件；若发现意外改动，已停止并上报而非 reset/stash。
- [ ] 路由契约清单由 `app.routes` 顺序元数据 + OpenAPI 合成（WebSocket / Mount / 静态路由不得仅靠 OpenAPI 覆盖）。
- [ ] `/api/ping` 与 `/api/qr-info` 已通过**接收窄依赖的 router 工厂**完成提取；router 不接收 `ApiServices`。
- [ ] 路由顺序、operation id、响应体、状态码、中间件行为与无重复注册保持不变（契约 diff 为空）。
- [ ] 迁移 2A 只转换行为等价的纯增量 `_ensure_*_columns` 方法；含自定义逻辑的方法登记为例外清单。
- [ ] 惰性调用点、事务/锁时机保留；SQL 标识符插值前经静态白名单校验。
- [ ] 空库 / 当前库 / 半迁移库 / 旧库四种 fixture 的幂等性测试通过。
- [ ] `tests/contracts/quality-baseline.json` 已 check-in：包含结构化 pytest/mypy 诊断与首次实测覆盖率；对比器对输出缺失或不可解析**判失败**。
- [ ] `tests/test_architecture.py` 对**新提取模块**强制 §3 依赖规则，并登记存量例外清单。
- [ ] 节点级 pytest 基线与归一化 mypy 基线（§7.2）无新增诊断。
- [ ] 新契约/质量检查已接入现有 `ci.yml`；发布工作流零改动。
- [ ] 被触碰模块的 `docs/modules/<module>.md` 与 `docs/changelog.md` 已更新。

### 10.2 后续批次（Should）

- [ ] `/api/health` 与 `/api/init-status` 提取，使用窄化 `HealthRouteDeps`。
- [ ] 事务/锁并发测试先行后，完成一个 repository 提取试点。
- [ ] Producer protocol + 帮助函数落地，并完成 **2 个**生产者迁移，共享契约套件复用。
- [ ] 一个 CLI 子命令组提取试点（前置：CLI 契约清单测试）。

### 10.3 终态（Phase 6–8 完成后）

- [ ] `api/app.py` 只剩组合/注册（目标 < 600 行）。
- [ ] `storage/database.py` 为纯兼容门面，不持有实现。
- [ ] `cli.py` 只剩组合/注册（目标 < 400 行）。
- [ ] 无新增可执行模块超过评审阈值 **800–1,000 行**；例外必须在模块 docstring 与本文件中登记。
- [ ] 架构依赖断言（§3 五条）在 CI 绿。
- [ ] 全部 7 个生产者通过共享契约套件。
- [ ] 四面行为矩阵覆盖用户可见功能。
- [ ] editable / Docker / 扩展 / 桌面打包检查在受影响时全绿。
- [ ] 验证/发布工作流所有权按 §4.7 落地：`ci.yml` 只验证、渠道工作流只发布、`verify-release-completeness.yml` 作 fan-in 门禁；工作流 PR 与应用重构 PR 始终独立。
- [ ] 推迟项（配置合并、框架迁移、双配置统一 RFC、已知失败测试的独立修复）显式记录在 §11，不散落 TODO。

---

## 11. 明确推迟或另行决策的事项

1. Schema 版本账 / 数据变换迁移（M-1 的 2B）——仅在有真实需求时提案，须具备原子迁移、半迁移恢复、备份与显式回滚语义。
2. 双配置合并 / 配置优先级变更（M-5，需 RFC）。
3. 前端框架迁移（M-6）。
4. HTTP API / WebSocket 协议重设计。
5. 已知失败测试 `test_aggregate_release_helper_does_not_backfill_previous_channel_assets` 的调查与修复（独立任务，需先审查其锁定的行为是否正确）。
6. `cli_models.py` 10 个既有 mypy 诊断——作为独立技术债任务单独跟踪；不属于本计划任何切片的承诺交付项。
7. `BaseProducer` 基类是否引入——推迟到 2 个生产者迁移证明存在稳定公共算法之后决策。
8. i18n 中文化解耦（清单 §12-5，独立工程）。
9. 发布工作流重构 / `workflow_call` 复用提取——即时切片只向 `ci.yml` 接入新检查；任何发布触发器、secret、产物布局调整都推迟且与应用重构 PR 严格隔离。

---

*本文档批准后即为后续实现与评审任务的执行基线。*
