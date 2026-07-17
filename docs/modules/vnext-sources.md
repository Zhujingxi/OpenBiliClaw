# vNext 多来源连接器与通用浏览器任务

> Runtime update: manifests/status/account configuration are exposed under
> `/api/v1/sources`; browser execution uses only generic
> `/api/v1/source-tasks` claim/complete. The extension uses one generic dispatcher.

## 状态与边界

本模块是权威 vNext 来源边界，提供七个平台的能力声明、只读连接器、retained client adapter 和通用浏览器任务服务。API 与 worker 的 production composition 显式注册全部七个平台，并通过无 live call 的 SQLite composition smoke test；`/api/v1/sources` 暴露 self-describing manifest、per-source settings read/write、secret-free status、write-only account configuration 与 typed idempotent disconnect，`/api/v1/source-tasks/claim` 与 `/api/v1/source-tasks/{task_id}/complete` 是唯一浏览器辅助任务 HTTP 合同。浏览器扩展通过一个 generic dispatcher 消费这些 route；旧平台 task endpoint 与 v0.3 producer 已不再是公开入口。

连接器只公开不可变 `ActivityEvent` 或 `ContentItem`。HTTP、CLI、SDK、DOM 原始 row 只能存在于 `infrastructure.sources.<platform>` 内部；不支持的能力不会用空结果或其它操作模拟，而是抛出 `UnsupportedSourceOperationError`。连接器不提供 like、follow、favorite、save、upvote、subscribe 等账号写操作。

## 七平台能力矩阵

manifest 把稳定产品能力与可执行操作分开。每个平台还附由真实 Pydantic model
导出的 `settings_schema` 与 write-only `credential_schema`；每个 operation 另带
`requires_auth`、`result_kind`、primary `transport_kind=direct|cli|browser`、可选
`fallback_transport_kind`，以及精确 `request_schema` / `result_schema`。schema 不含
credential default/example，generic UI 无需 source-specific arbitrary JSON contract。

| canonical source ID | 可执行只读操作 | transport 边界 |
|---|---|---|
| `bilibili` | bootstrap import、search、trending、related | retained `BilibiliAPI` direct primary；只有 direct client 明确发出 cooldown / DOM-fallback signal 时，search 才进入 durable browser fallback；没有平台原生 `explore` |
| `xiaohongshu` | bootstrap import、search、creator | durable queued browser transport |
| `douyin` | bootstrap import；search、trending、feed | bootstrap 固定 browser；discovery 由闭合 `direct|extension` mode 选择 retained direct client 或 browser |
| `youtube` | bootstrap import、search、trending、creator/channel | bootstrap browser；retained scraper direct adapter |
| `twitter` | bootstrap import、search、feed、creator | retained `XClient` / `twitter-cli` adapter；bootstrap 合并 likes + bookmarks |
| `zhihu` | bootstrap import、search、trending、feed、creator、related | durable queued browser transport |
| `reddit` | bootstrap import、search、trending、community/subreddit、related | bootstrap browser；closed `rdt|extension` backend 选择 retained CLI 或 browser |

creator 与 community 是独立能力；B 站跨域 explore 属于后续高层发现用例，不在 connector manifest。每个平台使用独立的冻结 Pydantic settings，但只公开真实 runtime consumer 已消费的字段：Bilibili、小红书、YouTube、Twitter、知乎 schema 为空；Douyin 仅保留 `mode=direct|extension`，由 connector/builder 选择 direct 或 browser transport；Reddit 仅保留 `backend=rdt|extension`，由 connector/builder 选择 CLI 或 browser transport。两个保留属性都携带 `x-consumer` schema metadata。来源启用、权重、schedule 与 Feed 限制统一由 global `UserSettings` 管理；不存在 per-source enabled、budget 或 interval 死开关。settings 不含 Cookie、token 或其它凭据字段。

## 公开 API

| 模块 | API |
|---|---|
| `features.sources.domain` | `SourceId`, `SourceCapability`, `SourceOperation`, `SourceOperationSpec`, `SourceManifest`, `SourceCredentialInput`, source-account status/disconnect, seven discriminated browser request/result variants, task models |
| `features.sources.registry` | `SourceRegistry`, `build_source_registry()`；函数签名显式要求七个已构造 connector，不扫描 entry point 或动态插件 |
| `features.sources.service` | `SourceTaskService.enqueue(request_deadline_at=...)`, `claim()`, `complete()`, `fail()`, `snapshot()`, `cancel()`；只允许 primary / fallback 含 browser 的 operation 进入 durable queue；`persistence_timeout_seconds` 与 SQLite busy timeout 对齐 |
| `infrastructure.sources.<platform>` | 严格 settings、production retained-client/CLI/browser adapter、connector 和显式 builder |
| `infrastructure.sources.browser_tasks` | `SQLAlchemyBrowserTaskRepository`, `QueuedBrowserTransport`；durable request deadline + execution wait + cancellation-resistant bounded compensation，原始 timeout/cancellation 在清理收敛或达到清理上限后传播 |
| `infrastructure.jobs.worker` | `build_default_source_registry(settings_overrides=...)` provider；API 每次操作、worker 每个 source/feed job 从持久化 settings 重建七个 built-in connector；保存前可用 candidate override 完成无 live-call preflight |

API composition 先创建 zero-I/O deferred registry provider；startup 必须先通过
`require_schema_at_head()`，随后才首次读取 per-source rows 并验证 registry。API services 每次操作都
从持久化 rows 解析新 registry，因此多个 API process/container 与 worker 共享同一 DB truth；
stale/unmigrated schema 会先得到权威 schema-head error，
不会在 container construction 期间被 source settings 读取掩盖。Worker 同样先执行 schema gate，
并在每个 source/feed job 开始时从当前持久化 settings 构造 registry。两条路径都只构造 connector 和 lazy credential/client provider，不读取凭据、
不创建 authenticated HTTP/CLI client，也不发起 live call。Bilibili、Douyin 与 X 的第一次
direct/CLI 调用才从 `source_accounts` 读取稳定排序后的 enabled account，并用
`CredentialCipher` 解密 Cookie；缺少 account、secret、有效密文或 Cookie 都抛出 typed
`MissingSourceConfigurationError`。小红书、知乎、YouTube bootstrap 和 extension-backend
Reddit 使用统一 `QueuedBrowserTransport`；YouTube public discovery 使用 retained scraper。
`UserSettings.sources.enabled` 默认全部为 false，所以默认 worker 不调用任何来源。

`GET/PUT /api/v1/sources/{source_id}/settings` 返回/合并 source package 自己的 strict
Pydantic settings。更新先以当前 persisted/default model 做 shallow patch、严格验证并执行同一
credential-shaped/non-finite safety check，再原子写入现有 `settings` table 的
`source-config:<source_id>` row。全局 `UserSettings` replace 会保留这些 namespaced rows。
返回值、OpenAPI 和持久化数据都不含 credential。schema-head gate 之后的 registry build 会
读取并重新验证这些 rows，再把 concrete settings 传入 connector builder；目前只有 Douyin
`mode` 与 Reddit `backend` 可写且会在该 build 中决定 transport。其它五个平台只接受空
object；已删除的 enabled/budget/interval/source-mode 字段会因 strict schema 被拒绝。API 的
settings candidate 会先作为 override 构造完整 registry，失败则 transaction 不写入；成功 commit 后
不依赖 process-local publish，所有 API 实例在下一次操作、worker 在下一次 job 自动读取新值。

`PUT /api/v1/sources/{source_id}/accounts` 只接受 account key 与 write-only cookie
credential，保存 opaque ciphertext 后只返回 configured/enabled status。`DELETE
/api/v1/sources/{source_id}/accounts/{account_key}` 删除加密账户 material；不存在时仍返回
`disconnected=true`，并以 `idempotent=true` 说明未发生第二次删除。GET、manifest、status、
disconnect 与 error payload 都不会返回 plaintext/ciphertext、credential key 或 form input。

## 规范化与身份

每个 connector 在 source package 内完成 URL、标题、作者、发布时间、内容类型与活动类型映射。`ContentItem.id` 由 `(source_id, external_id)` 生成稳定 UUID；`ActivityEvent.id` 由来源、活动类型、稳定外部 ID 与明确事件时间生成，重复 transport row 因此可幂等归一化。知乎 activity 优先使用 retained `interaction_time`，不会拿内容发布时间冒充互动时间。来源没有事件时间时不会猜测任务完成时间，而是使用明确的 epoch sentinel 并在 metadata 标记 `occurred_at_missing=true`。所有 content / activity operation 都把调用方 `limit` 传到 transport，规范化后再硬裁到 `N`；多 scope bootstrap 不能返回超过公开 limit 的结果。

## 通用浏览器任务安全

`SourceTaskRequest.payload` 是以 `operation` discriminated 的七种 typed request union：
`bootstrap_import(limit)`、`search(query,limit)`、`trending(limit)`、`feed(limit)`、
`related(seed,limit)`、`creator(creator,limit)`、`community(community,limit)`。completion 的
`result` 必须是同 operation 的 typed envelope `{operation, items}`； arbitrary payload 不再
是公开合同。service 只接受七个 canonical source ID，入队前核对 manifest 且只允许
primary 或 fallback 声明 browser 的 operation 持久化。claim 也只领取该来源的
browser-assisted operation。最早、仍在 `request_deadline_at` 之前的 pending /
lease-expired task 通过条件更新获得随机 lease token；独立 session 并发 claim 测试证明同一
任务只产生一个 owner。仅 lease 过期且请求未过期的任务可以重新领取，旧 token 不得
complete；到达 request deadline 的 pending / in-progress row 会被持久化为 `abandoned`，
claim 与 complete 的条件更新都排除它。deadline/lease 条件和新 lease/updated timestamp
均在原子 SQL 中使用 SQLite 数据库时钟求值，不使用进入 UoW 前捕获的 Python 时间，因此
写锁等待后不会凭陈旧授权领取或完成任务，也不会生成已经过期的 lease。

completion 要求 success `result` 与 `failure` 二选一。success 对相同 lease token + 相同结果是幂等的；failure 只接受闭合 code 与长度受限的异常类型，不接收或持久化页面错误 message。并行相同 completion 得到一次写入和一次 idempotent retry，并行不同结果只保留一个结果且另一方 conflict。payload/result 复用 frozen metadata 的严格 finite-JSON 验证，嵌套 `NaN` / `+Inf` / `-Inf` 在持久化前失败；normalized token classifier 递归拒绝 singular、plural、qualified 或嵌套的 `cookie_jar`、`authorization_header`、password、secret、session、credential、API key 和 token 容器。异常只报告字段路径，不回显字段值；窄 allowlist 中的 `token_count`、`session_duration`、`cookie_policy` 不会误伤。

`QueuedBrowserTransport` 在进入 enqueue 前预分配 task UUID，并把由 execution timeout 推导出的绝对 `request_deadline_at` 与任务一起持久化。execution timeout 或 asyncio cancellation 后，transport 会抗重复 cancellation 地等待在途 enqueue 得到确定结果；成功落库就同步等待 `cancel()` 写成终态，失败则只记录 exception class，再传播原始 timeout/cancellation。调用因此可超过 execution timeout，但不存在 detached callback/task 或 event-loop shutdown 后才出现的 pending insert。

已持久化 row 按 enqueue-time contract 依据其 source/operation/lease/deadline claim，而不依据后来改变的 transport manifest；所以 extension→direct 切换会阻止新 browser enqueue，同时允许切换前的合法 row 排空。

## 当前边界

- API 与 worker composition root 已构造七个平台 retained-client/CLI/browser adapter；manifest/status/account configuration 和 generic claim/complete 均为权威 `/api/v1` route。
- Web/extension generated client 与 generic dispatcher 已接线；扩展每轮从 `/api/v1/sources`
  刷新 manifest；manifest 与本地 executor 的交集决定哪些来源继续轮询，已返回的 durable
  claim 则按本地 executor 稳定能力校验，使 mode switch 前的 browser row 排空。deadline 在 executor 调用前
  校验，执行中到期会 abort 并清理 timer/listener/tab，迟到结果不能 success-complete。
- 旧平台 task endpoint、v0.3 producer、native account save 与动态插件发现不属于 vNext 公开合同。
- 历史数据库保持只读手工 archive，不导入 vNext；这不是待完成的数据迁移承诺。
