# vNext 用例与后台任务

> Runtime update: these services are injected into the authoritative
> `/api/v1` routers. Job progress reads application `job_runs`; Huey results are
> never exposed as product state.

## 状态与边界

本模块已实现 vNext 的 activity、profile、feed、library、chat 应用服务，以及独立 SQLite Huey worker。它是可运行的后台 composition，但尚未提供公开 HTTP API；Task 21 才会把这些用例接到 `/api/v1`。legacy backend 服务和现有前端路径在切换前保持不变。

feature service 只依赖自身声明的 repository、AI、settings 和 source Protocol，不导入 FastAPI、SQLAlchemy 或 Huey。SQLAlchemy adapter、共享 `TaskRunner` adapter、显式 `SourceRegistry` 与 Huey transport 都在 `infrastructure/` 组合。

## 已实现功能

| 用例 | 行为 |
|---|---|
| activity ingestion | 先幂等持久化不可变 `ActivityEvent`，再产生带原 event UUID 的确定性 `ProfileSignal`；显式画像编辑是 confidence=1 的 override |
| profile projection | 可通过 analysis lane 生成 typed `ProfileDelta`；proposal 携带应用拥有的 base revision，latest 已变化时拒绝陈旧 delta 并让 job 重算；unknown/duplicate evidence、重复 action 与 AI override 均被拒绝。所有送入投影的 evidence（含 narrative-only、no-op 或日后 facet 被删除）与新 revision 在同一 UoW 写入独立 consumed ledger |
| feed replenishment | 读取 typed source enable/weight settings，以稳定 SourceId tie-break 的 largest-remainder 算法精确分配有限候选预算；batch 前排除同 revision 已评估及历史 admitted/interacted/dismissed 内容，并有界扩量寻找新候选。所有评估都会持久化；topic hard cap 在任一 declared topic 饱和时拒绝该候选，只对实际 admitted 内容计数 |
| feedback | 同一事务写 `Interaction` 和确定性 feedback `ActivityEvent`；repository rank adjustment 会让后续排序读取该反馈 |
| library | 只写本地 `favorites` / `watch_later`，不调用平台账号 mutation |
| chat | 直接调用共享 `TaskRunner` 的 `obc-interactive` lane，不进入 Huey；持久化 user/assistant 两轮并输出可直接渲染为 SSE 的 typed delta/done chunks；opt-in learning 只写 activity evidence |
| background jobs | startup 重发全部 pending row，原子 claim 消解重复 Huey message；四类 feature 写事务内的 running guard 将 cancellation 与业务 effect 原子排序 |

## 四个任务与权威状态

只注册以下四个 Huey task：`source_sync`、`profile_projection`、`feed_replenishment`、`cleanup`。`source_sync` 执行已启用 connector 的真实 bootstrap activity operation；`profile_projection` 只处理 consumed ledger 尚未登记的事件；`feed_replenishment` 调用上述有界用例；`cleanup` 只删除超过保留期的 terminal `job_runs`。`source_sync` 的 Huey periodic wrapper 每分钟做一次轻量 tick，真实幂等时间桶读取 `UserSettings.source_sync_interval_minutes`；其它任务类型不增加。

Huey 使用独立 `data/vnext/huey.db`，开启 durable result storage、priority、有限 retry、periodic schedule 与 task lock；结果只属于 transport。应用先持久化 pending `job_runs`，再通过 TaskWrapper immediate enqueue 发布，成功后写 `dispatched_at`。queue failure 保留 undispatched row；重复 schedule 只 reconcile undispatched row，而 worker startup 会重新发布**全部** pending row，包括已有 marker 但可能已被 Huey dequeue、尚未完成应用 claim 的 row。enqueue/marker 或 dequeue/claim 任一窗口崩溃都允许产生重复 transport message，业务原子 claim 保证只有一个执行者；重启在 republish 中再次崩溃也不会重复业务 effect。产品状态、幂等键、协作式运行中取消、attempt、单调 progress、error/timestamps 全部以应用库为权威，`JobService.inspect()` 从不读取 Huey Result。consumer 异常退出后会把遗留 running 重置为 pending/undispatched，再与其它 pending row 一起重新发布。

四个 handler 都使用 `JobExecutionContext.checkpoint()` 在外部 source/model 边界后提交可见且不回退的 progress，并使用 `JobExecutionContext.guard()` 保护最终持久化。guard 不是独立的“先检查再写”：它在 activity、profile revision+consumed ledger、content+assessment+feed entry 或 terminal cleanup 的同一个 UoW 中，以条件 `running` update 取得 SQLite write lock 后才允许业务写入。cancellation 同样以 pending/running→cancelled 条件 UPDATE 作为事务的第一条语句，不先 SELECT 再升级读锁；checkpoint、succeed/fail/retry 与 running recovery 也采用 write-first 条件 SQL。若 cancellation 先取得写序，guard 等待后失败且整个 feature UoW 无 effect；若 guard 先取得写序，cancellation 按有限 `busy_timeout` 等待 feature effect 原子提交后再写 cancelled。锁等待耗尽会明确抛错，不会伪装成功。retry 保留已达到的最高 progress。

三个 priority 常量按 `interactive > user-triggered > scheduled-maintenance` 排序；chat 虽使用 interactive lane，但永不进入后台队列。worker 使用最多四个 thread workers，并通过 `python -m openbiliclaw.infrastructure.jobs.worker` 启动。源码与预构建 Compose 都挂载独立 Huey 文件；legacy `openbiliclaw-backend` 服务在 Task 21 前不改名。

## 配置与生产组合

`UserSettings.source_weights` 默认给七个平台相同合法权重，`source_enabled` 默认全部关闭。零权重来源不分配预算；负数、非有限权重和未知 SourceId 拒绝保存。worker composition 固定注册七个平台；启用来源缺少可用账户、凭据密文无法解密或缺少 Cookie 时，会以 `MissingSourceConfigurationError` 明确失败，不会发起匿名调用或伪装为空成功。

worker 默认迁移隔离 vNext 数据库，构造 SQLAlchemy UoW、`SettingsService`、LiteLLM `TaskRunner` 和真实四任务 orchestration。Compose 中 backend/API 与 worker 使用同一个 mounted `OPENBILICLAW_DATABASE_URL`，Huey 仍使用独立文件。production composition 逐项构造 Bilibili、小红书、抖音、YouTube、X、知乎与 Reddit connector，不扫描 entry point、不加载动态 source factory。direct/CLI client 在第一次真实调用时才从 `source_accounts` 读取 enabled account，并用 `CredentialCipher`/`OPENBILICLAW_SECRET_KEY` 解密；构造 registry 与全部来源 disabled 时不读取凭据、不创建网络 client。extension-assisted operation 统一使用 durable `QueuedBrowserTransport`；当前扩展 dispatcher 尚未切换，因此启用这类 operation 会等待 generic task callback。模型只读取 `OPENBILICLAW_LITELLM_BASE_URL` 与 `OPENBILICLAW_LITELLM_API_KEY`，provider credential 仍只存在于 LiteLLM。

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
