# Guided Init Module

## 概述

引导初始化（guided init）让用户既能在命令行 `openbiliclaw init`、也能在浏览器插件「推荐」tab、桌面 Web（`/web`）未初始化空状态、或安装包首启 `/setup/` 向导里点「开始初始化」完成首轮建模。所有图形入口共用同一套四阶段流水线，后端再叠加进度状态机、前置检查和写者门控，保证图形化初始化在一个活跃后端上安全运行。

命令行 `openbiliclaw init` 只在校验错误路径属于 `models.*`（或待迁移的 legacy `llm.*`）且当前为交互终端时，先后打开原生 Chat 与 Embedding 路由编辑器；其他配置错误直接失败，不会误入模型向导。两个编辑器都按 provider registry descriptor 只展示所选连接类型适用的字段，并统一经 `ModelConfigService` 校验和保存；init 模块不再维护 provider 专用菜单、TOML 写入器或独立校验规则。两个编辑器完成后会重新执行完整运行时配置校验，校验仍失败时在进入认证步骤前停止。非交互终端仍直接报告配置错误。

安装与排查统一使用 `openbiliclaw models list` / `add` / `edit` / `move` /
`remove` / `probe`。所有入口先选择 connection type，再只对该 type 支持的
preset 和字段提问；稳定 ID 与 ordered route 是跨 CLI、`/setup/`、bootstrap、
Docker 和 packaging 的共同契约。

四阶段（与 CLI 完全一致）：

1. **拉取数据** — B站 历史 / 收藏 / 关注（`_fetch_bilibili_init_data`，v0.3.118+ 仅当 `include_bili=True`，B 站与其他来源一样可取消）+ 小红书 / 抖音 / YouTube bootstrap 信号采集（按本轮勾选来源）+ 知乎 `bootstrap_events` + Reddit `bootstrap_events` + X 点赞 / 收藏（`_fetch_x_init_data`,服务端 twitter-cli 直拉、无扩展任务,与 B站 一样在本轮直接持久化;cookie 未同步时静默跳过）→ 统一 `build_event` → `memory.propagate_event` 入库。X 点赞 → `event_type="like"`、收藏 → `event_type="favorite"`(均为显式正向信号,v0.3.118+ 同时进画像构建的 history 行,保证 X-only 初始化也有画像输入)。Reddit saved → `favorite`、upvoted → `like`、subscribed subreddit → `follow`，每个分支默认最多 300 条；Reddit-only 初始化会等待插件回传这些信号，若 0 条则走统一 `empty_signals`。
2. **分析偏好** — `soul_engine.analyze_events(...)` 分片并发；每个初始化 chunk 除了结构化偏好，也会产出少量临时 `awareness_candidates` / `insight_candidates`，本地去重合并后只作为本次画像生成上下文。
3. **生成画像** ‖ 4. **发现补池**（并行）— `soul_engine.build_initial_profile(...)` 与发现补池同时跑，画像生成会消费合并后的 preference、history summary，以及第 2 段生成的临时觉察 / 洞察候选；这些候选不写入长期 `awareness` / `insight` 层。发现用 preference-only 草稿画像预热评估；如果正式候选池还是空的，补池会先构造 `cold_start` 的 `PoolDistributionSnapshot`，把画像中最高权重兴趣作为首批 query 的软避让方向，并优先覆盖次级兴趣 / 兴趣域，避免第一批 discovery 全部集中在同一个强 topic。

Issue #113 收口：阶段 2 偏好分析与阶段 3 画像生成各有 360 秒墙钟上限，超时会取消底层调用并形成可重试的硬失败；阶段 4 发现补池有 600 秒上限，超时沿既有部分成功语义完成。心跳只负责证明进程存活，不再掩盖一个永远没有终态的 provider 请求。硬失败 detail 会说明「AI 服务在 6 分钟内未返回」、常见的 Base URL / 模型名 / 网络 / 代理 / 服务过慢原因和模型设置测试 + 重试动作；discovery 超时则说明画像已生成、首池本次未完成、后台继续补池。

## 共享流水线 `cli.run_guided_init`

| 项 | 说明 |
|---|---|
| 位置 | `src/openbiliclaw/cli.py` |
| 签名 | `async run_guided_init(*, client, memory, soul_engine, favorite_limit, follow_limit, include_bili=True, include_xhs, include_dy, include_yt, include_x=False, include_zhihu=False, include_reddit=False, target_pool_count, discover_backfill, coordinator=None, run_id=None, profile_analysis_timeout_seconds=360, profile_build_timeout_seconds=360, discovery_timeout_seconds=600) -> InitResult`（`include_bili=False` 时 `client` 可为 `None`；timeout 传 `<=0` 可供受控调用方关闭对应上限） |
| 为什么是协程 | 四阶段原先内联在 `init` 命令里，被四处独立 `asyncio.run` 包着，后端无法复用（会嵌套事件循环）。合并为一个协程后，CLI 用单次 `asyncio.run(run_guided_init(...))` 驱动、API 在服务 loop 里直接 `await`。 |
| bootstrap 采集器 | 仍是同步实现（有同步调用方 + 测试），但在流水线里走 `await asyncio.to_thread(...)`，不冻结 API 事件循环；`Database` 以 `check_same_thread=False` 打开，跨线程读安全。 |
| `discover_backfill` 注入 | 唯一与运行路径相关的步骤。CLI 传 `_run_init_discovery_backfill_async`（一次性 `discovery_engine`）；API 传 `controller.run_init_backfill`（持 `_refresh_lock`，与连续 refresh 串行）。其余步骤完全共享。 |
| 进度上报 | 传入 `coordinator` / `run_id` 时，在每个 stage 边界回调 `coordinator.stage_started/stage_done`、并 `register_enqueued_task` 登记 bootstrap task id；run 生命周期（mark_running / complete / fail）留给调用方。v0.3.162+ 增加阶段内子进度生产者：阶段 1 在每个所选数据源的采集边界回调 `stage_progress(1, done=已完成源数, total=所选源数, note="正在采集 <平台>")`（B 站算第一个源）；阶段 2 给 `soul_engine.analyze_events` 传 per-chunk `progress_callback`，API 路径映射为 `stage_progress(2, done, total, note="第 d/t 批")`，CLI 路径（coordinator 为 None）打印 `分析偏好：第 d/t 批完成`（与 eta 倒数并存）。阶段 3/4 是单次 LLM 调用无天然进度点，靠 eta + 心跳。 |
| 失败语义 | 硬失败抛 `GuidedInitError(reason)`（`empty_history`（选了 B 站但历史为空）/ `empty_signals`（所有所选画像来源 0 信号，v0.3.118+）/ `analyze_failed` / `profile_failed`）：CLI 转状态面板 + 退出码 1，API 转 `coordinator.fail(reason, detail=message)`。偏好 / 画像超时分别复用后两个 reason，detail 给出超时含义、常见配置 / 网络原因和模型设置测试 + 重试动作；发现阶段失败或 600 秒超时是部分成功（画像已生成），`InitResult` 除 `discovery_error` / `discover_exc` 外还带 `discovery_reason` / `discovery_detail`，超时 reason 为 `discovery_timeout`。 |

`InitResult` 携带 CLI summary / API wrapper 需要的全部字段（各来源事件数、scope counts、profile、`discovered_count`、`discovery_error` / `discover_exc`、`discovery_reason` / `discovery_detail`）。API wrapper 用最后两个字段完成可诊断的部分成功终态，CLI warning 面板也显示同一文案。

首轮发现多样性由 `discovery.pool_snapshot.build_cold_start_pool_snapshot()` 提供：当 CLI `_run_init_discovery_backfill_async` 或 API `ContinuousRefreshController.run_init_backfill()` 看到 `count_pool_candidates()==0` 时，会生成 `cold_start=true` 的 snapshot 并传给 `ContentDiscoveryEngine.discover(..., pool_snapshot=...)`。这份 snapshot 不代表真实池子已有饱和历史；它只把权重最高的 1-2 个兴趣当作 `avoid_topics` 软约束，把剩余兴趣名和一级兴趣域放入 `prefer_axes`，让搜索词 prompt 在保留少量强兴趣命中感的同时，把首批内容面铺开。池子已有内容后，API runtime 会改用真实 `build_pool_distribution_snapshot()`；CLI 首轮 init 只做空池冷启动保护。初始化完成后的统一 keyword planner 如果遇到正式池仍为空，也会把同一套 cold-start hints 写进各平台的 merged keyword prompt，避免跨平台第一批关键词都押在同一个强兴趣上。

## 状态机 `InitCoordinator`

| 项 | 说明 |
|---|---|
| 位置 | `runtime/init_coordinator.py`；惰性挂在 `RuntimeContext.init_coordinator`（重建后仍读当前组件） |
| 持久化 | `init_runs` 表（`storage/database.py`）：`run_id / status / stage / stages_json / partial_success / error_reason / error_detail / sequence / started_at / updated_at / finished_at`。`error_detail`（v0.3.156+，存量库自动 ALTER 迁移）存失败细节：未知异常的 `类名: 首行消息`（截断 300 字）或 `GuidedInitError` 的人类可读 message，`fail(run_id, reason, detail=...)` 落库；v0.3.168+ `complete(partial_success=True, reason=..., detail=...)` 也保留 discovery 降级原因并随 `init_completed` 事件下发。重新预约同 run_id 时清空。 |
| 单飞启动 | `try_start(run_id)` → `try_reserve_init_starting`（`BEGIN IMMEDIATE` CAS）；活跃 run 存在时返回 False。TOCTOU 收口在 DB。 |
| 单写者 | `_write(...)` 在 `_write_lock` 下串行化「读 stages → 改 → 写 → 发事件」，保证并行 stage 3/4 的 `sequence` 严格递增、不丢更新。 |
| 事件 | `init_progress`（stage 起止）/ `init_completed` / `init_failed`，经 `event_hub` 推到 `runtime-stream`。 |
| 取消 | `attach_task` 记任务句柄；`cancel_current_run` 调 `task.cancel()`，wrapper 捕获 `CancelledError` 后 shield 写入 `cancelled` 终态。 |
| 启动 reconcile | `reconcile_on_boot()`（API startup 调用）把崩溃残留的 `starting/running` 行判 `failed(interrupted)`，避免 `/api/init-status` 永远报 running。 |
| bootstrap 归属 | `register_enqueued_task` / `is_owned_bootstrap_task` 给写者门控判断某 task-result 是否属于本 init run。 |
| 阶段子进度（v0.3.162+） | `stage_progress(run_id, stage, *, done, total, note=None)`：向对应 stage dict 写 `progress={done,total,note}`（clamp `0≤done≤total`，`total≤0` 整个写入忽略——不落库、不 bump sequence、不发事件），并发布携带 progress 的 `init_progress` 事件。`stage_done` / 终态失败会清掉该 stage 的 progress，避免完成的 stage 挂着陈旧的「第 3/8 批」。 |
| 心跳 `touch()`（v0.3.162+） | 空 `_write`：只 bump `sequence` + `updated_at`，**不**发布事件（活性由前端 3s 轮询读出，SSE 只留给真实进度变化）。 |
| 阶段 eta（v0.3.162+） | `_initial_stages()` 每个 stage 带 `eta_seconds`（`_STAGE_ETAS = {1:90, 2:180, 3:70, 4:120}`；2/3 迁移自 CLI eta 常量，provider 换代需复核 calibration）。 |
| `last_activity`（v0.3.162+） | `get_status()` 透出 `run["updated_at"]`（无 run 时 `""`）——任何 stage / 子进度 / 心跳写入都会刷新，前端据此算停滞时长。 |

前置探测 `InitPrereqs`（`runtime/init_prereqs.py`）：`chat_ready()` 对 production `OrderedLLMRoute.connections` 按配置顺序逐个调用 exact adapter health，primary 不健康会继续 fallback，第一条健康即通过；legacy `registry.get()` 只服务显式注入的旧 context（成功 TTL 300s / 失败 8s，超时判不就绪）、`bilibili_check()`（`validate_cookie`，ok 60s / fail 10s TTL）、`peek_chat()` / `peek_bilibili()`（只读缓存值、不发探针）、`enabled_platforms()`；v0.3.152+：`GET /api/init-status` 在 `initialized && !running` 时改用 peek 值，不再对已初始化实例发真实（计费）chat 探针或 B 站往返——此前开着 `/setup/` 或桌面 Web 等首池页面会每 30s 烧一条 5-in/10-out 的 "hi" 补全；`POST /api/init`（含 force 重建）仍做实时复验，桌面 Web 在 `initialized` 后也不再渲染前置 checklist。embedding readiness 复用 `/api/health` 的 `_health_embedding_ready()`，绕过缓存真实调用一次 `EmbeddingService.probe()`；`/api/init-status` 与 `POST /api/init` 显式使用 strict 解释，同一缓存结果为 `timed_out` 时仍下发 `embedding_ready=false` / 返回 409，只有真实非空向量成功才放行，普通 health 对本地 Ollama 冷加载超时的容忍不会渗入初始化门禁。全部探测都 TTL 缓存 + 单飞，避免轮询打爆。`/api/init-status.prerequisites.embedding_required` 由原生 `[models.embedding]` 的 enabled 状态与 ordered provider list 决定；已启用时 `can_start` 会硬性等待 `embedding_ready=true`，`POST /api/init` 临界区也会复验，失败返回 `409 embedding_not_ready` 并把刚预约的 run 回滚为 idle。空 Provider list 代表用户明确关闭 Embedding，仍允许降级初始化。v0.3.118+：B 站登录不再硬性拦截 `GET /api/init-status` 的 `can_start`（是否拦截取决于客户端勾选了哪些来源，只有 `POST /api/init` 知道）——`bilibili_logged_in` 仍在 `prerequisites` 里下发，前端在勾选了 B 站时自行拦截；`POST /api/init` 也只在所选来源包含 bilibili 时做登录 409 复验。显式 `sources` 为空或没有任何合法平台 key 时返回 409 `no_sources_selected`；其余合法勾选（包括 Reddit-only）会作为本轮显式 opt-in 生效，并 best-effort 写回 `sources.<platform>.enabled=true`。v0.3.153+：B 站探测恒直连——`BilibiliAPIClient` `trust_env=False`，不再继承环境变量 / 系统代理（代理出口 IP 常触发 B站 风控，已登录用户显示"未登录"；开着 Clash 等代理无需任何操作即可通过检测），网络必须走代理时用 `[bilibili].proxy` 显式指定；探测失败时把失败原因下发到 `prerequisites.bilibili_detail`（`POST /api/init` 的 409 响应同样带 `detail`）——`AuthStatus.network_error` 区分传输层失败与 Cookie 真失效（-101 归 Cookie 类，不误导查代理），传输类 detail 按实际链路给排查提示（直连失败 → 查本机网络 / TUN 全局模式加直连规则；显式代理失败 → 检查该代理或清空改回直连）；两处 Web checklist 的 B 站行 label 按探测真实结果措辞（"登录检测未通过"），未通过时不再出现"已登录"字样，hint 展示 `bilibili_detail`。

## API 端点

| 端点 | 方法 | 访问 | 说明 |
|---|---|---|---|
| `/api/init-status` | GET | 远程可读 / 降级可读 | 权威进度 + 前置清单 + `can_start`（trusted-local && 硬前置 && 非 running && supported）/ `can_manage`（trusted-local）。前置清单包含 `embedding_ready` 与 `embedding_required`；v0.3.155+ 还包含 `embedding_check` / `embedding_detail`——向量模型未就绪的分类原因（`disabled` / `misconfigured`（provider 名无效，如被浏览器整页翻译写坏）/ `not_running` / `model_missing` / `model_broken` / `model_path_encoding` / `disk_full` / `network` / `model_oom` / `provider_error`，Ollama 路径经 `llm/ollama_diagnostics.py` 真实分类、失败 TTL 缓存），三端向量模型行 hint 直接展示 `embedding_detail`。`model_path_encoding` 表示 Windows 非 ASCII 用户名导致模型 blob 路径无法被 `llama-server` 加载，提示迁移模型目录或手动设置 `OLLAMA_MODELS`，不建议重复拉取到原路径；`disk_full` / `network` / `model_oom` 是手动处理型原因，分别提示清理磁盘、修网络 / 代理 / 镜像源、释放内存或换更小 embedding 模型。v0.3.155+ reason 梯子补上 not-trusted 分支：非本机（手机扫码 / 局域网）查看时 reason=`local_only` 而非 `none`，三端显示「只能在本机发起初始化」，不再出现全绿清单配「以下条件未满足」的矛盾文案。v0.3.156+：上次 run 失败 / 取消时顶层 `detail` 下发 `init_runs.error_detail`（异常摘要 / `GuidedInitError` message），三端失败文案渲染为「通用文案（具体原因）」，未映射的 typed reason（`empty_history` / `empty_signals` / `profile_failed`）直接显示其 message；`interrupted` / `cancelled` 补进三端 reason 映射。v0.3.162+ 过程可见性字段（全部 optional 向后兼容）：`stages[].progress`（`{done,total,note}`，运行中 stage 的子进度，如阶段 2 分片批次）、`stages[].eta_seconds`（阶段典型耗时提示）、顶层 `last_activity`（最近一次状态写入的时间戳，含 30s 心跳——前端 >90s 无变化即显示停滞提示）。Issue #113 收口后，未初始化且没有活跃 run 时还会读取 `AccountSyncService.last_account_sync_error`：画像分析失败会进入顶层 `detail`；当前 chat 探针失败时 reason 保持 `llm_not_ready`，探针已恢复时 reason=`analyze_failed` 且 `can_start` 仍允许重试。已生成画像但 discovery 超时 / 失败时返回 `partial_success=true`、持久化的 `discovery_timeout` / `discovery_partial` 与人类可读 detail，不再被 `already_initialized` 覆盖；三端可据此说明部分完成。远程不 403、`can_manage=false`。 |
| `/api/init` | POST | 仅本机 | 占坑前廉价拒绝（403 local_only / 409 unsupported_runtime / 409 already_initialized）→ `try_start`（409 already_running）→ 临界区复验前置（缺则复位 idle + 409，不留 stuck `starting` 行；包括已配置 embedding provider 时的 `embedding_not_ready`）→ 后台跑 wrapper → 202 + 初始 status。v0.3.162+：命中 `embedding_not_ready` 时会 best-effort 调用 `_maybe_autostart_embedding_pull()`；仅 Ollama provider、`model_missing` / `model_broken`、loopback endpoint（含 `127.0.0.1:11435`）且磁盘守卫通过才复用现有修复锁与任务启动拉取，远程 / Docker 主机名及其他诊断不自动操作。409 始终带 `detail`：已拉取时为实时进度，未拉取时指向「修复向量模型」。可选 body `sources`（平台来源数组）：传入时按合法平台 key 直接作为本轮显式 opt-in，并 best-effort 写回 `sources.<platform>.enabled=true`；不传则用全部已开启平台（CLI / 旧客户端行为）。 |
| `/api/init/cancel` | POST | 仅本机 | 协作取消在跑的 run；无运行中 → 409 not_running。 |
| `/api/embedding/repair` | POST/GET | POST 仅本机 / GET 公开 | v0.3.155+ 一键修复向量模型：POST 先经 `diagnose_ollama_embedding()` 分类（已就绪 → 200 `already_ok` 并立即过期就绪缓存；`not_running` → 409 附排查提示；非 ollama provider → 409 `unsupported_provider`；修复已在跑 → 409 `already_running`）。诊断、not-running start、provider-error restart gate、model-path migration gate 与后台 pull 全部使用当前 Ollama Embedding provider 的精确 daemon root；即使 Chat 同时指向 `11434`，Embedding 指向 `11435` 也不会启动/重启 Chat daemon。`model_path_encoding` 只会尝试迁移同一受管 endpoint；ownership 不匹配、无安全目录或检测到外部 Ollama时返回 409 `manual_fix_required` / `external_ollama`。缺失 / 损坏则启动单飞后台任务经 Ollama `/api/pull` 拉取（202）；GET 回报 `{running, status, completed, total, done, ok, error}` 供前端显示进度，成功后过期 `_health_embedding_ready` 缓存。拉取期间 `init-status` 的 `embedding_check="repairing"`、`embedding_detail` 带实时百分比，并下发 `embedding_repair_running/completed/total`；`/setup/`、桌面 Web 与 popup 复用这些字段显示进度和按诊断选择的修复按钮。 |

v0.3.157+：`/api/init-status.prerequisites` 还会下发 `ollama_phase`（`starting` / `ready` / `down`）和 `embedding_pull_status`。这两个字段来自进程全局 `runtime.embedding_progress`，因此桌面包首启后台自动拉取 `bge-m3` 与用户点击 `/api/embedding/repair` 共享同一套进度；只要任一路径正在拉取，`embedding_check` 会优先报 `repairing`，`embedding_repair_running/completed/total` 也会反映该进度。`/api/embedding/repair` 遇到 `not_running` 时，只有配置允许托管且精确 Embedding root 是默认 11434 或已记录的私有 daemon（例如 11435）才会先拉起并重新诊断；远端、未记录自定义端口、不同 endpoint ownership 或 `manage_ollama=false` 返回 409。

v0.3.157+：`/api/embedding/repair` 是有界的「诊断 → 修复 → 重新诊断」编排器，最多执行 3 次自动动作。`not_running` 只对上述精确受管 root 尝试拉起；`model_missing` / `model_broken` 启动单飞 pull 前检查磁盘；`model_path_encoding` 只迁移同一受管 root 的模型目录；`provider_error` 也只在精确 root 与当前 managed record 匹配时尝试一次 spec-aware restart。`disk_full` / `network` / `model_oom` 直接返回 409，不启动无意义 pull。

`_init_wrapper`（`api/app.py`）是某次 API run 的**唯一**状态 / 事件写者：`mark_running` → `run_guided_init(coordinator=...)` → `complete(partial_success=..., reason=discovery_reason, detail=discovery_detail)`；`CancelledError` → shield `cancel`，`GuidedInitError` → `fail(reason, detail=exc.message)`，其它异常 → `fail("internal_error", detail=_init_crash_detail(exc))`（`类名: 首行消息`，截断 300 字——v0.3.156+ 失败原因可从 UI 报告，无需翻服务端日志）。v0.3.162+ 的自动拉取发生在启动端点把任务交给 wrapper 之前：自愈诊断、调度失败都会回落原 409 主路径，调度失败用 `mark_pull_done(False, error)` 回滚拉取态而不伪造 Ollama phase；因此 wrapper 的单写者契约不变。三个 path 都在 `auth.py` 公共集 + 降级白名单。v0.3.162+：wrapper 在 `mark_running` 后启动一个 30s 周期的 heartbeat task（`_run_init_heartbeat` → `coordinator.touch(run_id)`，touch 失败吞掉 log WARNING、绝不杀 init），`finally` 里取消——长请求等待期间 `last_activity` 保持 ≤30s 新鲜（前端 90s 停滞阈值 = 心跳周期 × 3，改周期须同步改阈值）；Issue #113 收口后心跳不再无限续命，阶段 2/3/4 分别会在 360/360/600 秒进入失败或部分成功终态。

## init 期间写者门控

防止并发写污染在跑的 init（`init_active()` 为真时）。设计原则是 **deny-by-default**：不是枚举"要拦的写端"（总会漏），而是默认拦截一切变更、只放行 init 必需的少数路径。

- **HTTP 写端（deny-by-default）**：`_init_active_write_guard` 中间件对所有 `POST/PUT/PATCH/DELETE` 返回 `409 init_running`,**除非**命中放行清单：`/api/init`、`/api/init/cancel`、`/api/bilibili/cookie`、`/api/auth/*`、以及精确 5 段匹配的 `/api/sources/<source>/{kick,task-result}`（bootstrap 协议)。
- **副作用 GET**：写者门控只拦变更方法,所以两个会写状态的"读"另行处理:`GET /api/recommendations` 的空历史 bootstrap `serve()`(写推荐行 / 标记 shown)在 init 期跳过；`GET /api/sources/*/next-task` 的 init ownership filter 只约束 legacy discovery/bootstrap queues（`next_pending(only_ids=…)`），避免陈旧任务饿死本轮采集器；durable native-save job 保持 native-first priority，不被 init-owned legacy ID 集过滤。
- **后台循环**：`background_llm_work_allowed()`（account_sync / startup one-shot）+ `ContinuousRefreshController._llm_work_allowed()`（连续 refresh / soul pipeline / producer，经注入的 `init_active_check`）在 init 期一律返回 False。init 自身不受影响。安装包 `/setup/` 的模型保存走 revisioned `PUT /api/model-config`；`ModelConfigService` 与 runtime coordinator 在同一事务边界完成 candidate swap、旧 graph 清退和后台恢复，不再通过 `/api/config` 的模型字段或 suppression 开关旁路。
- **cookie 例外**：`/api/bilibili/cookie` 在 init 期间:同值 200 no-op、异值 409(均不 validate / 不 rebuild,避免换掉正在用的客户端)。
- **task-result 例外**：`/api/sources/*/task-result` 放行,但 handler 在 init 期**跳过所有发现池写**;仅对 **init-owned**(`is_owned_bootstrap_task`)结果走 propagate(经既有 bootstrap-key 去重),并跳过增量画像管线(`_ingest_profile_update_events`)——新画像由 stage 2/3 从采集事件统一构建。
- **热重载豁免**：`rebuild_from_config` 的 `cancel_all(exclude={"guided_init"})` 让 init 任务不被配置热重载取消。

> 该门控经 9 轮 Codex 对抗验收收敛(2 high + 多 medium 修复),最终 PASS。唯一已知遗留是 bootstrap-key 去重的非原子窗口(load→propagate→mark),为 **gui-init 之前就存在**的共享 task-result 行为、低概率、轻影响,列为独立硬化 follow-up。

## 图形 UI（extension / web）

推荐 tab 未初始化空状态给「开始初始化」面板：数据来源勾选（v0.3.118+ B 站默认勾选但可取消，小红书 / 抖音 / YouTube / X / 知乎 / Reddit 一样可选，至少保留一个数据来源；配「需在本浏览器登录目标平台」文案）+ 按钮（点击驱动校验：点击时拉 `/api/init-status`，一个来源都没勾 → 提示「至少勾选一个数据来源」，勾选的小红书 / 抖音 / YouTube / X / 知乎 / Reddit 会作为本轮 opt-in 并自动开启对应来源，勾了 B 站但未登录 → 提示登录或取消勾选，前置未通过 → 展示前置清单 + 原因、不启动；全通过才带所选 `sources` 启动）+ 启动后进度条，详见 [extension 模块文档](extension.md)。桌面 `/setup` 与 `/web` 会按 `embedding_required` 把向量模型显示为硬前置或可降级项。DOM 无关逻辑在 `extension/popup/popup-init-control.js`，单测在 `extension/tests/init-control.test.ts`。

桌面 `/setup/` 的首启模型步骤已切到原生 ordered-route API。它只创建
第一条 Chat 记录，或编辑快照中现有的第一条稳定-ID Chat 记录；已有
fallback 顺序、Embedding Provider 顺序及共享 settings 都按公开 payload
原样保留，不在首启页提供完整多路由管理。需要增删或重排 route 时使用
桌面/插件/移动端 Models 设置页，或 `openbiliclaw models ...`。首启页左侧
是一列 Chat connection type，右侧为 descriptor-driven inspector；窄屏时
上下堆叠，类型列表仍保持单列。

向导先读取 `GET /api/model-connection-types?capability=chat` 和脱敏的
`GET /api/model-config`。选择 connection type 后，只渲染 descriptor
允许的 preset / model / Base URL / credential 字段；API-key compatible
家族保持在各自协议类型下，`codex_oauth` 独立。inline secret 永不回读，
已有 credential 只显示 configured/source，并提交 `keep` / `set` / `env` /
`clear` 动作。空 Chat snapshot 会创建全局不与 Embedding ID 冲突的
`chat-primary` 草稿；若该 ID 已占用则确定性追加数字后缀。

保存顺序固定为：先 `POST /api/model-config/probe` 精确探测当前选中的
draft（不走 fallback），成功后携带读取到的 revision 调用
`PUT /api/model-config` 保存完整公开 route。409 冲突会装载服务端最新
snapshot、保留可重试提示，不用旧 revision 覆盖并发修改。模型字段不再
写入 `/api/config`；该接口在 setup 中只继续处理 B 站 Cookie 等非模型
配置。模型保存由 `ModelConfigService` 协调 runtime swap 与 init 写门控。

模型步骤完成后，向导进入 B 站与来源选择，展示同款前置清单，使用
`POST /api/init` 启动和 `runtime-stream` / 轮询恢复进度。向导 load 时会
读 `/api/init-status`：`running` 直接恢复进度，`initialized` 按首池状态
进入完成页或「整理首轮内容池」。安装包入口（`packaging/entry.py`）
在打开浏览器前轮询 `/api/health`，再按 init 状态选择 `/setup/` 或
`/web/`；它只通过 typed `[models]` helper seed Embedding，不做 legacy
model mutation。

首启模型下载可见性：当桌面包在后台自动拉取 `bge-m3`（约 568MB）时，`/setup/` 与 `/web` 会和手动修复一样显示 `.init-progress` 进度条、`embedding_pull_status` 文案和「Ollama 启动中…」阶段提示；下载失败或中断后不再隐藏手动修复按钮，用户仍可点击「自动下载向量模型」重试。

运行中进度可见性（v0.3.162+，init-progress-visibility）：三个 GUI 面（popup / 桌面 `/web` / `/setup/` 向导）的进度公式同构升级——运行中 stage 的贡献从固定 0.5 半格改为：有 `progress` 时 `min(0.95, done/total)`（阶段 2 每完成一个分片进度条就前进）、无 progress 但有 `eta_seconds` 时 `min(0.95, 1−e^(−elapsed/eta))`（elapsed 从该 stage 首次被观察到 running 的 client 时刻起算）、两者皆无（旧后端）回退 0.5 常量保持历史刻度；3+4 并发取均值；渲染 pct 按 run_id 做单调 clamp 永不回退。运行行拼入子进度 note（`2/4 分析偏好 · 第 3/8 批`）与「本阶段通常约 X 分钟」；进度条下方常态显示「● 进行中」，`last_activity` 超过 90s 无变化（按 client 观察时刻计、免时钟偏移）转 amber 停滞文案「后台已 N 分钟没有新进展…可以继续等待，或取消后重试」；idle 面板加「整个过程通常需要 2–5 分钟，期间可离开此页面，进度会保留」预期文案。参考实现是 `extension/popup/popup-init-control.js` 的纯函数（`initProgressView` / `stalenessView` / `stageEtaText`），desktop `app.js` 与 setup 向导内联 JS 逐字镜像——三面技术栈不同无法下沉共享，是四面契约允许的例外；桌面既有的首池等待 `pct:95` 与 embedding 下载借位两个覆盖态不受影响。

超时错误态（v0.3.168+）：三端均优先展示后端 typed detail，而不是短 reason label。`analyze_failed` / `profile_failed` 会在进度区显示具体步骤、6 分钟未返回的含义、常见原因和恢复动作；后台 account-sync 在探针仍失败时虽然 reason 是 `llm_not_ready`，只要 detail 以 `画像分析失败：` 开头也走同一优先级，避免机器码 / 通用门禁提示盖住根因。`partial_success + discovery_timeout/discovery_partial` 在首池等待态显示部分完成 detail；popup 的 `init_completed` 事件同样保留 warning。进度 / 原因节点使用 `aria-live`，硬失败切为 `role=alert` / assertive，普通进度保持 polite，避免每次轮询都强打断读屏。

## 测试

- `tests/test_init_coordinator.py` — 协调器生命周期 / 单飞 / 并行 stage / reconcile / 取消 / 接线 / `/api/init-status` 形状 / 门控后台暂停，以及部分成功 reason/detail 的持久化与事件透传。
- `tests/test_init_prereqs.py` — 前置探测 TTL / 乐观超时。
- `tests/test_database.py` — `init_runs` CAS / 白名单列 / reconcile。
- `tests/test_api_app.py::TestGuidedInitEndpoints` — `/api/init`、`/api/init/cancel` 守门（403 / 409 各路径、复位不留 stuck 行）+ 写者门控（events 409 / cookie no-op / task-result 放行）+ 真实 `/api/init` handler 通过 `InitCoordinator` 向 `/api/runtime-stream` 发 `init_progress` / `init_completed` 的后端契约。
- `tests/test_cli.py` — `openbiliclaw init` 全回归（共享流水线零回归）。
- `extension/tests/init-control.test.ts` — 清单 / 按钮态 / 进度状态机纯函数；v0.3.162+ 增加分片进度 pct、eta 伪进度、单调 clamp（20 步含乱序模拟序列非降且终值 100）、90s 停滞文案与旧 status 兼容用例；v0.3.168+ 覆盖硬超时、account-sync detail-first 与 discovery 部分成功文案。
- `tests/test_web_guided_init.py` — 安装包 `/setup/` 与桌面 `/web` 未初始化空状态的 guided-init 接线静态合约；覆盖 API 模型 progress/eta/last_activity、heartbeat、阶段 1/2 进度生产者，以及阶段 2/3 超时取消和阶段 4 超时部分成功。
- `tests/test_desktop_web_init_progress.py` — 桌面 `/web` 与 `/setup/` 向导镜像实现的字符串级合约（公式要素 / clamp / 停滞与预期文案 / 覆盖态保留）。
- `tests/test_web_guided_init_e2e.py` — Playwright 驱动真实 `/setup/` 与 `/web` 页面，stub 外部 HTTP 响应来覆盖浏览器交互：成功进度、前置失败、启动冲突、终态重试、runtime-stream 静默 watchdog，PC setup / Web 等首批 `pool_available_count>0` 后才完成，以及 PC Web 与插件一致的未初始化入口判断（已有推荐 / 候选池信号时不再弹引导）；v0.3.168+ 直接断言超时原因、Base URL / 模型设置恢复动作、重试 / 设置入口、`role=alert`，以及 discovery 超时的部分完成 / 后台补池提示。CI 的 `web-guided-init-e2e` job 安装 `[browser]` extra + Chromium 后单独运行。
- 完整真号 GUI init（插件推荐 tab → 前置清单 → 开始 → 进度 → 画像 → 推荐）列入用户手测 DoD。
