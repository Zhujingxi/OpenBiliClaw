# 架构设计

## vNext 领域、薄 `/api/v1`、独立 worker 与 generated clients

backend-first vNext 已交付冻结 Pydantic 领域契约与纯策略、七平台 connector 与 generic browser task、SQLAlchemy/Alembic persistence、类型化设置和 Fernet 凭据、只经 LiteLLM 的 PydanticAI typed-task 边界，以及 activity/profile/feed/library/chat application service 和独立 Huey worker。领域与 feature service 不导入 FastAPI、SQLAlchemy 或 Huey；来源原始 HTTP/CLI/SDK/DOM row 只存在于各自 infrastructure source package。

```text
retained transports (HTTP / CLI / logged-in extension tab)
        │ raw rows stay inside each source package
        ▼
7 explicit built-in SourceManifest + Connector adapters
        ├─► immutable ActivityEvent / ContentItem ─► application services
        └─► generic SourceTaskService ─► source_tasks (request deadline + lease/cancel/abandon)
        │ normalized domain objects
        ▼
features: Activity ──► Profile ─────┐
          Content  ──► Assessment ──┼─► Feed / Interaction / Collection
          Chat / Sources / System ──┘
        │ repository ports / typed settings
        ▼
infrastructure.database
        ├─ SQLAlchemy mappings + repositories + UnitOfWork
        ├─ installer / Compose one-shot migrate ─► Alembic 0001 + 0002 ─► data/vnext/openbiliclaw.db
        ├─ API + worker startup ─► read-only schema-head gate
        └─ settings / source_accounts / activity / profile + consumed evidence / content
           / auth_state / feed / collections / chat / source_tasks / job_runs / ai_runs
        │
        └─ infrastructure.security.CredentialCipher
              OPENBILICLAW_SECRET_KEY ──► derived Fernet key ──► opaque ciphertext only

profile/feed/chat use cases ──► reusable TaskSpec + PydanticAI Agent
        │                          │ typed input/output + semantic retries only
        │                          ▼
        │                    infrastructure.ai.TaskRunner ──► ai_runs metadata only
        │                          │ SDK network retries = 0
        │                          ▼
        ├─ interactive ─────► obc-interactive ─┐
        ├─ analysis ────────► obc-analysis ────┼─► LiteLLM proxy ─► providers
        └─ embedding client ► obc-embedding ───┘     │ routing/fallback/retry/
                                                     │ limits/cache
                                                     └─► LiteLLM PostgreSQL

Huey scheduler/transport (data/vnext/huey.db, result enabled)
        └─► source_sync / profile_projection / feed_replenishment / cleanup
              └─► JobService ─► job_runs (all-pending recovery/claim/cancel/txn guard)

Existing Web + Extension (OpenAPI-generated clients)
        └─► cookie+CSRF / finite extension bearer / fetch-SSE
              └─► FastAPI feature routers (/api/v1 only)
                    ├─► auth status/login/logout/exchange/revoke
                    ├─► injected application services
                    ├─► SSE chat + onboarding/job progress
                    └─► generic source-task long poll claim/complete

Implemented: domain contracts/policies; seven source manifests/connectors/settings;
             lease-safe generic source tasks; isolated schema/migration; repository/UoW;
             credential cipher; six typed AI tasks/runner/embedding/health; application services;
             explicit seven-source worker composition; four durable jobs; LiteLLM/Huey Compose;
             thin FastAPI v1 routers, cookie/CSRF + bearer auth, SSE, operational CLI,
             deterministic OpenAPI and unified error envelope
Implemented: web/extension generated clients, cookie/bearer auth, fetch-SSE, generic browser dispatcher
Deferred: final unreachable legacy-tree deletion; stored legacy data is not migrated
```

vNext 数据库默认 URL 是 `sqlite:///data/vnext/openbiliclaw.db`，与 legacy 数据库隔离。`DatabaseSettings` 可读取 `OPENBILICLAW_DATABASE_URL` / `OPENBILICLAW_DATABASE_ECHO` / `OPENBILICLAW_DATABASE_BUSY_TIMEOUT_SECONDS`；SQLite driver timeout 与 `PRAGMA busy_timeout` 使用同一个有限值。`SettingsService` 对完整 `UserSettings` 做严格校验后才在一个事务中替换；来源账户 repository 只接受 `CredentialCipher` 签发的 opaque Fernet ciphertext。

`UserSettings` 以 `sources/schedules/feed/profile/tasks/network/logging/access_control/jobs`
九个 strict nested group 表达实际运行选择。log directory、worker concurrency、bearer/password
configured flags 是明确的 deployment/read-only facts，不接受 PATCH。installer bearer、Web password
hash、session signing secret 与 extension device-key digest records 只来自私密 runtime environment；
password cookie 与 extension finite bearer 共用签名 session，但 unsafe cookie request 还必须通过
same-origin + `X-OBC-Auth` CSRF gate。Alembic `0002_auth_state` 的非秘密 epoch 为全局 session
revocation authority，递增后所有旧 Web/extension session 失效，installer bearer 不受影响。
Extension origin 永远不能使用 loopback trust 或 CORS bypass，只能先 exchange device key 再显式
携带 finite bearer；login/device exchange 分别使用 per-peer bounded failure limiter。vNext auth
只读 environment，不 fallback legacy config。startup reconciliation 将无密码建模为显式状态：
fresh absent 不写 row；首次 enable 不 bump；rotation、removal (`disabled`) 与 re-enable 都在同一
事务更新 password state 并 increment epoch，重复 unchanged/disabled 幂等。这样旧 session 不会
在密码移除后继续有效，也不会因恢复同一 hash 而复活；reconcile 失败则 session auth fail closed。

七个平台 registry 只在 composition time 显式构造，不扫描动态插件。connector manifest 将稳定产品能力与 concrete operation 分开，每个 operation 声明 auth、normalized result kind、primary transport 和可选 fallback transport；B 站 search 是 direct primary + 仅在 retained risk-control signal 下启用的 browser fallback，explore 保留在高层 discovery，不冒充平台原生操作。现有 Bilibili API、Douyin direct、YouTube scraper、X client、Reddit CLI 均有 production adapter，登录态页面操作使用 typed durable queue。generic task 只接受 manifest 中 primary/fallback browser-assisted operation，payload/result 先做 finite JSON 校验，再以 token classifier 拒绝 qualified credential container。enqueue 时持久化绝对 request deadline；claim/complete 在原子 SQL 内以 SQLite 数据库时钟重查 deadline/lease，并从同一时钟生成新 lease，避免写锁等待后使用陈旧 Python 时间；到期 row 由 snapshot/claim 收敛为 `abandoned`。execution timeout / asyncio cancellation 之后运行独立的 cancellation-resistant cleanup，最多等待与 SQLite busy timeout 对齐的有限 persistence window 再传播原异常；因此总返回时间可超过 execution timeout，且 cleanup 超界时不承诺返回前已经写成 `cancelled`，但 durable deadline 保证 row 不再 actionable。超界的 late enqueue 由 done callback 安全取走 outcome，日志只保留异常类名，不泄露异常文本或触发未取回 task exception。并发 claim 只有一个 lease owner；相同 completion 幂等，不同结果冲突。详细矩阵见 [vNext 多来源连接器与通用浏览器任务](modules/vnext-sources.md)。

AI application 代码只允许 `obc-interactive`、`obc-analysis`、`obc-embedding` 三个稳定别名。`TaskRunner` 仅做输入/输出验证、usage/timeout 限制和 bounded semantic retry；`CachePolicy.BYPASS` 只转发 LiteLLM `cache.no-cache` 请求指令，provider deployment、fallback、网络重试、限流和 cache 实现全部由 LiteLLM 拥有。六个 task 覆盖 profile、keyword、单候选、batch candidate、chat 和 recommendation；profile/feed worker 与 `/api/v1/chat/stream` 已使用共享 runner adapter，chat history 由 `/api/v1/chat/{conversation_id}` 做 bounded public projection。AI health 只从显式 `OPENBILICLAW_LITELLM_ADMIN_URL` 投影可选安全导航 URL，不暴露 internal base/key。四份 versioned Pydantic Evals dataset 继续覆盖既有核心任务。`ai_runs` 结构只含 task/model/status/timing/usage/error class，没有输入或输出 payload。详细契约见 [vNext 类型化 AI 模块](modules/vnext-ai.md)。

Library list 在 repository 以单次 join 返回 collection membership 与 normalized content；profile
PATCH 把 narrative/facet override evidence、expected-revision check 与一个新 revision 原子提交，
并给新 revision/evidence 一个严格晚于旧 revision 的 aware UTC timestamp。
Source manifest 自描述 safe form schemas 和七类 operation request/result，account disconnect 只删除
encrypted material 并返回 idempotent status。Per-source GET/PUT settings 复用 `settings` table 的
namespaced rows；API container 先创建 zero-I/O deferred holder，startup 通过 schema-head gate 后才
读取 rows 并安装 registry。五个平台 per-source schema 为空；Douyin 只保留已消费的 transport
`mode`，Reddit 只保留已消费的 `backend`，enabled/weights/schedule/feed policy 属于 global
`UserSettings`。Worker 在启动 composition/recovery/consumer 前安装或复用 owned console/file
sinks 并作用 persisted network/logging policy；退出或失败时保留 host handlers/root policy，只
清理本次创建的 sinks，同时精确恢复 proxy、package logger 与四个 CA 环境变量。FastAPI 的 centralized error mapper 与 deterministic
OpenAPI post-processor统一使用 `{error:{code,message}}`，不覆盖 success/security/SSE metadata，
Starlette 404/405 也走该 envelope；边界不向客户端泄露 traceback、SQL、credential 或 provider text。

worker production composition 固定构造全部七个平台，不加载动态插件。direct/CLI client 只在首次调用时读取 `source_accounts` 并用 `CredentialCipher` 解密；默认全部来源 disabled，registry 构造不会发起网络调用。DB→Huey 采用 pending commit、immediate enqueue、`dispatched_at` marker；启动会重新发布全部 pending row，因此 Huey 已 dequeue、应用尚未 claim 的 message 也可恢复，重复消息由原子 claim 消解。Huey 只负责 transport、priority、periodic、retry 和 lock，产品状态、取消和 progress 只读应用库 `job_runs`。Docker Compose 以唯一一次性 `migrate` 服务串行 Alembic 写入，并以 successful-completion dependency 阻止失败时启动 API/worker；两个 runtime startup 只执行 schema-head 只读 gate。Source installer 也在启动两个进程前独占 migration。FastAPI 已切到注入式 feature router 与 `/api/v1`，CLI 只保留运行/诊断/评测/数据库命令；旧 app/CLI 不再是入口。现有静态 Web 与扩展已消费这些 route：Web 使用 cookie + CSRF，扩展使用 finite bearer，SSE 统一使用 authenticated fetch stream，浏览器任务统一经 generic claim/complete dispatcher。

Source-install stable-root boundary 由 held checkout root、append-only root guard 与内层 lifecycle anchor 组成。Guard 对每代写入相同 pending/committed record 并校验完整历史；只在 active lease 内恢复 generation 0 初始 pending，或同 root/instance/anchor 恰落后一代的 installer/process record。环境、installer 与 process state 的 replacement 保留 temp FD、只对 FD 改 mode，并在 replace 前后验证 inode，失败不 pathname-unlink 不确定 temp；Windows temp 由 `CreateFileW(CREATE_NEW)` 以 read/write/delete sharing 打开并把 handle ownership 转交给仍跨越 `os.replace` 的 CRT FD。Windows runtime logs 以不共享 delete 的 native reparse-aware handles 固定目录与 final；POSIX 保持 held dirfd。Queue health 正常 pathname connect 后要求全部新增普通 FD 都来自预先固定的 main/WAL/SHM identity set。Backup publication 在 Linux 保持 anonymous payload + `linkat`；macOS 从 locked held payload FD `fclonefileat`，并以 32 个固定、成功后 descriptor-zero/reuse、失败时有界保留的槽消除 temp pathname authority。

## 已停止作为入口的 v0.3 实现

v0.3 的 provider 路由、Soul/awareness/probe、保存同步、主动通知、自更新、平台专用任务端点和 runtime socket 已被 vNext 取代，不再是当前架构，也不属于受支持的公开接口。其实现细节仅保留在 Git 历史、历史版本标签和明确标注的 changelog archive 中；本文件不再复述旧运行时，以免读者误将历史设计视为现行合同。
