# vNext 用例与后台任务

> Runtime update: these services are injected into the authoritative
> `/api/v1` routers. Job progress reads application `job_runs`; Huey results are
> never exposed as product state.

## 状态与边界

本模块实现权威 vNext 的 activity、profile、feed、library、chat 应用服务，以及独立 SQLite Huey worker。这些用例已注入 `/api/v1` feature routers，并由 Web 与 extension generated clients 消费。

feature service 只依赖自身声明的 repository、AI、settings 和 source Protocol，不导入 FastAPI、SQLAlchemy 或 Huey。SQLAlchemy adapter、共享 `TaskRunner` adapter、显式 `SourceRegistry` 与 Huey transport 都在 `infrastructure/` 组合。

## 已实现功能

| 用例 | 行为 |
|---|---|
| activity ingestion | 先幂等持久化不可变 `ActivityEvent`，再产生带原 event UUID 的确定性 `ProfileSignal` |
| profile projection/edit | analysis lane 生成 typed `ProfileDelta`；proposal 携带应用拥有的 base revision，latest 已变化时拒绝陈旧 delta 并让 job 重算。显式 `ProfileEdit` 创建一条 confidence=1 override evidence，支持 narrative 与五类 facet upsert/removal；expected revision、去重/钳制和同一 UoW 保证一次请求恰生成一个 revision，冲突整体回滚；revision/evidence 使用严格晚于上一 revision 的 fresh timestamp |
| feed replenishment | 读取 typed source enable/weight settings，以稳定 SourceId tie-break 的 largest-remainder 算法精确分配有限候选预算；batch 前排除同 revision 已评估及历史 admitted/interacted/dismissed 内容，并有界扩量寻找新候选。所有评估都会持久化；topic hard cap 在任一 declared topic 饱和时拒绝该候选，只对实际 admitted 内容计数 |
| feedback | 从已持久化的 normalized `ContentItem` 读取 title/summary/topic 形成有语义的 evidence，并在同一事务写 `Interaction` 与 feedback `ActivityEvent`。read-side rank 以 immutable assessment score 加持久化 interaction adjustment 返回当前 feed；后续 profile projection 把负反馈转成 avoidance，使 assessor 能降低语义相近的全新候选 |
| library | 只写本地 `favorites` / `watch_later`，不调用平台账号 mutation；list 用一次 join 返回 collection metadata + renderable `ContentItem`，按 `added_at,id` 稳定排序 |
| chat | 直接调用共享 `TaskRunner` 的 `obc-interactive` lane，不进入 Huey；持久化 user/assistant 两轮并输出可直接渲染为 SSE 的 typed delta/done chunks；history 按 conversation 隔离、升序、有界分页且移除 AI run metadata；opt-in learning 只写 activity evidence |
| background jobs | startup 重发全部 pending row，原子 claim 消解重复 Huey message；四类 feature 写事务内的 running guard 将 cancellation 与业务 effect 原子排序 |

## 四个任务与权威状态

只注册以下四个 Huey task：`source_sync`、`profile_projection`、`feed_replenishment`、`cleanup`。`source_sync` 执行已启用 connector 的真实 bootstrap activity operation；`profile_projection` 只处理 consumed ledger 尚未登记的事件；`feed_replenishment` 调用上述有界用例；`cleanup` 只删除超过保留期的 terminal `job_runs`。`source_sync` 的 Huey periodic wrapper 每分钟做一次轻量 tick，真实幂等时间桶读取 `UserSettings.schedules.source_sync_interval_minutes`；其它任务类型不增加。

Huey 使用独立 `data/vnext/huey.db`，开启 durable result storage、priority、有限 retry、periodic schedule 与 task lock；结果只属于 transport。应用先持久化 pending `job_runs`，再通过 TaskWrapper immediate enqueue 发布，成功后写 `dispatched_at`。queue failure 保留 undispatched row；重复 schedule 只 reconcile undispatched row，而 worker startup 会重新发布**全部** pending row，包括已有 marker 但可能已被 Huey dequeue、尚未完成应用 claim 的 row。enqueue/marker 或 dequeue/claim 任一窗口崩溃都允许产生重复 transport message，业务原子 claim 保证只有一个执行者；重启在 republish 中再次崩溃也不会重复业务 effect。产品状态、幂等键、协作式运行中取消、attempt、单调 progress、error/timestamps 全部以应用库为权威，`JobService.inspect()` 从不读取 Huey Result。consumer 异常退出后会把遗留 running 重置为 pending/undispatched，再与其它 pending row 一起重新发布。

四个 handler 都使用 `JobExecutionContext.checkpoint()` 在外部 source/model 边界后提交可见且不回退的 progress，并使用 `JobExecutionContext.guard()` 保护最终持久化。guard 不是独立的“先检查再写”：它在 activity、profile revision+consumed ledger、content+assessment+feed entry 或 terminal cleanup 的同一个 UoW 中，以条件 `running` update 取得 SQLite write lock 后才允许业务写入。cancellation 同样以 pending/running→cancelled 条件 UPDATE 作为事务的第一条语句，不先 SELECT 再升级读锁；checkpoint、succeed/fail/retry 与 running recovery 也采用 write-first 条件 SQL。若 cancellation 先取得写序，guard 等待后失败且整个 feature UoW 无 effect；若 guard 先取得写序，cancellation 按有限 `busy_timeout` 等待 feature effect 原子提交后再写 cancelled。锁等待耗尽会明确抛错，不会伪装成功。retry 保留已达到的最高 progress。

三个公开 priority lane 按 `interactive > user-triggered > scheduled` 排序；chat 虽使用 interactive lane，但永不进入后台队列。worker 使用最多四个 thread workers，并通过 `openbiliclaw worker` 或 `python -m openbiliclaw.worker` 启动。源码与预构建 Compose 都让 API/worker 挂载同一个独立 Huey 文件。

## 配置与生产组合

`UserSettings.sources.weights` 默认给七个平台相同合法权重，
`UserSettings.sources.enabled` 默认全部关闭。零权重来源不分配预算；负数、非有限权重和
未知 SourceId 拒绝保存。per-source schema 不重复这些全局控制：六个平台为空，Douyin 只用
`mode` 选择 direct/browser；Reddit 全部 retained operation 固定使用 browser extension。worker composition 固定注册
七个平台；需要 direct authentication 的启用来源缺少可用账户、凭据密文无法解密或缺少 Cookie 时，会以
`MissingSourceConfigurationError` 明确失败，不会发起匿名调用或伪装为空成功。

worker 默认只读验证隔离 vNext 数据库已经位于 Alembic head，再读取 persisted
`UserSettings`。它先安装或复用 OpenBiliClaw-owned console 与 rotating-file sinks（deployment
默认 `logs/openbiliclaw.log`），保留 host-owned handlers 与 root logger level，再在构造
registry、恢复任务和启动 consumer 前应用 network proxy 及 persisted console/file levels。
正常退出或 consumer/runtime 构造失败时只移除并关闭本次 worker 创建的 sinks，复用的 owned
sinks 与 package logger 状态恢复原值；logging level 在 cleanup 前恢复。network teardown 同时
恢复此前 proxy，以及 `SSL_CERT_FILE`、`SSL_CERT_DIR`、`REQUESTS_CA_BUNDLE`、
`CURL_CA_BUNDLE` 四个 CA 环境变量进入 scope 前的精确存在性和值。随后构造 SQLAlchemy UoW、
`SettingsService`、LiteLLM `TaskRunner` 和真实四任务 orchestration。Compose 中唯一一次性
`migrate` 服务先完成 schema 写入；失败时 `service_completed_successfully` dependency 阻止
API/worker 启动。backend/API 与 worker 使用同一个 mounted
`OPENBILICLAW_DATABASE_URL`，Huey 仍使用独立文件。production composition 逐项构造
Bilibili、小红书、抖音、YouTube、X、知乎与 Reddit connector，并从 `settings` table 的
`source-config:*` rows 恢复各 package settings；不扫描 entry point、不加载动态 source
factory。direct/CLI client 在第一次真实调用时才从 `source_accounts` 读取 enabled account，
并用 `CredentialCipher`/`OPENBILICLAW_SECRET_KEY` 解密；构造 registry 与全部来源 disabled
时不读取凭据、不创建网络 client。extension-assisted operation 统一使用 durable
`QueuedBrowserTransport`；extension 的单一 generic dispatcher 通过 `/api/v1/source-tasks`
claim/complete 消费这些任务，并按 manifest 与本地 executor 能力交集轮询。模型只读取 `OPENBILICLAW_LITELLM_BASE_URL` 与
`OPENBILICLAW_LITELLM_API_KEY`，provider credential 仍只存在于 LiteLLM。

## 公开 Python API

- `features.activity.service.ActivityService`
- `features.profile.service.ProfileService`
- `features.feed.service.FeedService`, `FeedbackService`, `FeedPolicy`, `allocate_source_limits()`
- `features.library.service.LibraryService`
- `features.chat.service.ChatService`, `ChatChunk`
- `infrastructure.ai.use_cases` 的三个共享 TaskRunner adapter
- `infrastructure.jobs.tasks.JobService`, `JobExecutionContext`（含 transaction-scoped `guard()`）, `JobRunSnapshot`, 四个 task wrapper
- `infrastructure.jobs.orchestration.build_worker_runtime()`
- `infrastructure.jobs.worker.build_default_source_registry()`
- `infrastructure.jobs.worker.run_worker()`

这些是 Python composition API，不是公开 HTTP API 合同。
