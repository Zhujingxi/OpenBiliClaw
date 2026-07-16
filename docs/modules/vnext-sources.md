# vNext 多来源连接器与通用浏览器任务

## 状态与边界

本模块为 backend-first vNext 提供七个平台的能力声明、只读连接器边界、现有客户端适配器和通用浏览器任务服务。它已经通过逐操作 connector、真实客户端形状、SQLite 并发与排队等待合同测试，但**尚未接入当前生产 API、legacy runtime 或浏览器插件 dispatcher**；HTTP 路由、composition root 和扩展切换由后续任务完成。当前 v0.3 来源模块与各平台旧任务 endpoint 仍是实际运行路径。

连接器只公开不可变 `ActivityEvent` 或 `ContentItem`。HTTP、CLI、SDK、DOM 原始 row 只能存在于 `infrastructure.sources.<platform>` 内部；不支持的能力不会用空结果或其它操作模拟，而是抛出 `UnsupportedSourceOperationError`。连接器不提供 like、follow、favorite、save、upvote、subscribe 等账号写操作。

## 七平台能力矩阵

manifest 把稳定产品能力与可执行操作分开。每个 operation 另带 `requires_auth`、`result_kind`、primary `transport_kind=direct|cli|browser` 和可选 `fallback_transport_kind`，因此同一个 `trending/feed` 能力可以有多个真实操作，混合 transport 也不会隐身，但不会把高层探索策略伪装成平台原生操作。

| canonical source ID | 可执行只读操作 | transport 边界 |
|---|---|---|
| `bilibili` | bootstrap import、search、trending、related | retained `BilibiliAPI` direct primary；只有 direct client 明确发出 cooldown / DOM-fallback signal 时，search 才进入 durable browser fallback；没有平台原生 `explore` |
| `xiaohongshu` | bootstrap import、search、creator | durable queued browser transport |
| `douyin` | bootstrap import；search、trending、feed | bootstrap 固定 browser；discovery 由闭合 `direct|extension` mode 选择 retained direct client 或 browser |
| `youtube` | bootstrap import、search、trending、creator/channel | bootstrap browser；retained scraper direct adapter |
| `twitter` | bootstrap import、search、feed、creator | retained `XClient` / `twitter-cli` adapter；bootstrap 合并 likes + bookmarks |
| `zhihu` | bootstrap import、search、trending、feed、creator、related | durable queued browser transport |
| `reddit` | bootstrap import、search、trending、community/subreddit、related | bootstrap browser；closed `rdt|extension` backend 选择 retained CLI 或 browser |

creator 与 community 是独立能力；B 站跨域 explore 属于后续高层发现用例，不在 connector manifest。每个平台使用独立的冻结 Pydantic settings；所有 mode/backend/source-mode 都是 `Literal` 或枚举闭集，settings 只含开关、模式、预算与节流值，不含 Cookie、token 或其它凭据字段。

## 公开 API

| 模块 | API |
|---|---|
| `features.sources.domain` | `SourceId`, `SourceCapability`, `SourceOperation`, `SourceOperationSpec`, `SourceManifest`, `SourceConnector`, browser task models |
| `features.sources.registry` | `SourceRegistry`, `build_source_registry()`；函数签名显式要求七个已构造 connector，不扫描 entry point 或动态插件 |
| `features.sources.service` | `SourceTaskService.enqueue(request_deadline_at=...)`, `claim()`, `complete()`, `snapshot()`, `cancel()`；只允许 primary / fallback 含 browser 的 operation 进入 durable queue；`persistence_timeout_seconds` 与 SQLite busy timeout 对齐 |
| `infrastructure.sources.<platform>` | 严格 settings、production retained-client/CLI/browser adapter、connector 和显式 builder |
| `infrastructure.sources.browser_tasks` | `SQLAlchemyBrowserTaskRepository`, `QueuedBrowserTransport`；durable request deadline + execution wait + cancellation-resistant bounded compensation，原始 timeout/cancellation 在清理收敛或达到清理上限后传播 |

## 规范化与身份

每个 connector 在 source package 内完成 URL、标题、作者、发布时间、内容类型与活动类型映射。`ContentItem.id` 由 `(source_id, external_id)` 生成稳定 UUID；`ActivityEvent.id` 由来源、活动类型、稳定外部 ID 与明确事件时间生成，重复 transport row 因此可幂等归一化。知乎 activity 优先使用 retained `interaction_time`，不会拿内容发布时间冒充互动时间。来源没有事件时间时不会猜测任务完成时间，而是使用明确的 epoch sentinel 并在 metadata 标记 `occurred_at_missing=true`。所有 content / activity operation 都把调用方 `limit` 传到 transport，规范化后再硬裁到 `N`；多 scope bootstrap 不能返回超过公开 limit 的结果。

## 通用浏览器任务安全

`SourceTaskRequest` 只接受七个 canonical source ID 与闭合 `SourceOperation`；service 在入队前核对 manifest 且只允许 primary 或 fallback 声明 browser 的 operation 持久化。claim 也只领取该来源的 browser-assisted operation。最早、仍在 `request_deadline_at` 之前的 pending / lease-expired task 通过条件更新获得随机 lease token；独立 session 并发 claim 测试证明同一任务只产生一个 owner。仅 lease 过期且请求未过期的任务可以重新领取，旧 token 不得 complete；到达 request deadline 的 pending / in-progress row 会被持久化为 `abandoned`，claim 与 complete 的条件更新都排除它。

complete 对相同 lease token + 相同结果是幂等的；并行相同 completion 得到一次写入和一次 idempotent retry，并行不同结果只保留一个结果且另一方 conflict。payload/result 复用 frozen metadata 的严格 finite-JSON 验证，嵌套 `NaN` / `+Inf` / `-Inf` 在持久化前失败；normalized token classifier 递归拒绝 singular、plural、qualified 或嵌套的 `cookie_jar`、`authorization_header`、password、secret、session、credential、API key 和 token 容器。异常只报告字段路径，不回显字段值；窄 allowlist 中的 `token_count`、`session_duration`、`cookie_policy` 不会误伤。

`QueuedBrowserTransport` 在进入 enqueue 前预分配 task UUID，并把由 execution timeout 推导出的绝对 `request_deadline_at` 与任务一起持久化；enqueue + poll 的浏览器执行等待使用该 timeout。timeout 或调用方取消后，另一个 cleanup task 会在显式有限的 persistence bound 内等待在途 insert 并尝试写 `cancelled`；该 bound 默认是与 SQLite `busy_timeout_seconds` 对齐的 service persistence timeout 加短 scheduling grace。父 task 再次被 cancel 不会越过 cleanup task；cleanup 完成或达到自身上限后才重新抛出最初的 `TimeoutError` / `CancelledError`，cleanup 异常只按类型安全记录，不替换原异常。

这里没有“调用必在 execution timeout 的同一时刻返回”或“返回前一定已经写成 cancelled”的承诺：总等待最多还包含有限 cleanup window。若 insert/cancel 因数据库阻塞超过 cleanup bound，调用可在 row 尚未收敛前返回；row 随后落库时已携带过期 deadline，因此永远不能新 claim，下一次 snapshot/claim 会把它持久化为 `abandoned`。成功 compensation 的 row 是 `cancelled`；两种状态都不可 claim/complete，且不会留下 actionable orphan。扩展仍在自己的登录 tab 执行任务，task payload 不承载浏览器 Cookie。

## 尚未完成

- 尚未新增 `/api/v1/source-tasks` HTTP claim/complete 路由。
- 尚未把 legacy 各平台 task endpoint 或扩展 dispatcher 改接通用合同。
- 各平台 retained-client/CLI/browser adapter 与 builder 已实现，但尚未在生产 composition root 构造并切换 runtime。
- 尚未切换 legacy 数据或停止 v0.3 来源 producer。
