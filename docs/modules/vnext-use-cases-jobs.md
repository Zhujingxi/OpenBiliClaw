# vNext 用例与后台任务

## 状态与边界

本模块已实现 vNext 的 activity、profile、feed、library、chat 应用服务，以及独立 SQLite Huey worker。它是可运行的后台 composition，但尚未提供公开 HTTP API；Task 21 才会把这些用例接到 `/api/v1`。legacy backend 服务和现有前端路径在切换前保持不变。

feature service 只依赖自身声明的 repository、AI、settings 和 source Protocol，不导入 FastAPI、SQLAlchemy 或 Huey。SQLAlchemy adapter、共享 `TaskRunner` adapter、显式 `SourceRegistry` 与 Huey transport 都在 `infrastructure/` 组合。

## 已实现功能

| 用例 | 行为 |
|---|---|
| activity ingestion | 先幂等持久化不可变 `ActivityEvent`，再产生带原 event UUID 的确定性 `ProfileSignal`；显式画像编辑是 confidence=1 的 override |
| profile projection | 可通过 analysis lane 生成 typed `ProfileDelta`；应用规则拒绝未知/重复 evidence、重复 upsert/removal、AI override，并在一个 UoW 内 optimistic append 一个 revision |
| feed replenishment | 读取 typed source enable/weight settings，以稳定 SourceId tie-break 的 largest-remainder 算法精确分配有限候选预算；只调用 manifest 支持的只读 operation，按 `(source_id, external_id)` 去重，只做一次有界 batch assessment，再执行 score、novelty、source/topic diversity admission |
| feedback | 同一事务写 `Interaction` 和确定性 feedback `ActivityEvent`；repository rank adjustment 会让后续排序读取该反馈 |
| library | 只写本地 `favorites` / `watch_later`，不调用平台账号 mutation |
| chat | 直接调用共享 `TaskRunner` 的 `obc-interactive` lane，不进入 Huey；持久化 user/assistant 两轮并输出可直接渲染为 SSE 的 typed delta/done chunks；opt-in learning 只写 activity evidence |

## 四个任务与权威状态

只注册以下四个 Huey task：`source_sync`、`profile_projection`、`feed_replenishment`、`cleanup`。`source_sync` 执行已启用 connector 的真实 bootstrap activity operation；`profile_projection` 只处理最新画像尚未引用的事件；`feed_replenishment` 调用上述有界用例；`cleanup` 只删除超过保留期的 terminal `job_runs`。

Huey 使用独立 `data/vnext/huey.db`，开启 durable result storage、priority、有限 retry、periodic schedule 与 task lock；结果只属于 transport。产品状态、幂等键、取消、重启恢复、attempt/progress/error/timestamps 全部以应用库 `job_runs` 为权威，`JobService.inspect()` 从不读取 Huey Result。consumer 异常退出后，启动入口会把 application DB 中遗留的 `running` 重置为 `pending` 并重新排队。

三个 priority 常量按 `interactive > user-triggered > scheduled-maintenance` 排序；chat 虽使用 interactive lane，但永不进入后台队列。worker 使用最多四个 thread workers，并通过 `python -m openbiliclaw.infrastructure.jobs.worker` 启动。源码与预构建 Compose 都挂载独立 Huey 文件；legacy `openbiliclaw-backend` 服务在 Task 21 前不改名。

## 配置与生产组合

`UserSettings.source_weights` 默认给七个平台相同合法权重，`source_enabled` 默认全部关闭。零权重来源不分配预算；负数、非有限权重和未知 SourceId 拒绝保存。worker composition 固定注册七个平台；启用来源缺少可用账户、凭据密文无法解密或缺少 Cookie 时，会以 `MissingSourceConfigurationError` 明确失败，不会发起匿名调用或伪装为空成功。

worker 默认迁移隔离 vNext 数据库，构造 SQLAlchemy UoW、`SettingsService`、LiteLLM `TaskRunner` 和真实四任务 orchestration。production composition 逐项构造 Bilibili、小红书、抖音、YouTube、X、知乎与 Reddit connector，不扫描 entry point、不加载动态 source factory。direct/CLI client 在第一次真实调用时才从 `source_accounts` 读取 enabled account，并用 `CredentialCipher`/`OPENBILICLAW_SECRET_KEY` 解密；构造 registry 与全部来源 disabled 时不读取凭据、不创建网络 client。extension-assisted operation 统一使用 durable `QueuedBrowserTransport`；当前扩展 dispatcher 尚未切换，因此启用这类 operation 会等待 generic task callback。模型只读取 `OPENBILICLAW_LITELLM_BASE_URL` 与 `OPENBILICLAW_LITELLM_API_KEY`，provider credential 仍只存在于 LiteLLM。

## 公开 Python API

- `features.activity.service.ActivityService`
- `features.profile.service.ProfileService`
- `features.feed.service.FeedService`, `FeedbackService`, `FeedPolicy`, `allocate_source_limits()`
- `features.library.service.LibraryService`
- `features.chat.service.ChatService`, `ChatChunk`
- `infrastructure.ai.use_cases` 的三个共享 TaskRunner adapter
- `infrastructure.jobs.tasks.JobService`, `JobRunSnapshot`, 四个 task wrapper
- `infrastructure.jobs.orchestration.build_worker_runtime()`
- `infrastructure.jobs.worker.build_default_source_registry()`
- `infrastructure.jobs.worker.run_worker()`

这些是 Python composition API，不是公开 HTTP API 合同。
