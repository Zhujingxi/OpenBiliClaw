# vNext 多来源连接器与通用浏览器任务

## 状态与边界

本模块为 backend-first vNext 提供七个平台的能力声明、只读连接器边界和通用浏览器任务服务。它已经实现并通过 mock transport / SQLite 合同测试，但**尚未接入当前生产 API、legacy runtime 或浏览器插件 dispatcher**；HTTP 路由、composition root 和扩展切换由后续任务完成。当前 v0.3 来源模块与各平台旧任务 endpoint 仍是实际运行路径。

连接器只公开不可变 `ActivityEvent` 或 `ContentItem`。HTTP、CLI、SDK、DOM 原始 row 只能存在于 `infrastructure.sources.<platform>` 内部；不支持的能力不会用空结果或其它操作模拟，而是抛出 `UnsupportedSourceOperationError`。连接器不提供 like、follow、favorite、save、upvote、subscribe 等账号写操作。

## 七平台能力矩阵

| canonical source ID | 保留能力 | transport 边界 |
|---|---|---|
| `bilibili` | activity import、search、trending、related、explore | 现有 API / extension transport 的只读适配 seam |
| `xiaohongshu` | activity import、search、creator | 已登录扩展 tab |
| `douyin` | activity import、search、trending、recommended feed | direct HTTP 或已登录扩展 tab |
| `youtube` | activity import、search、trending、creator/channel | scraper、Takeout 或扩展 transport |
| `twitter` | search、recommended For-You、creator | `twitter-cli` 只读 transport；没有虚构 bootstrap |
| `zhihu` | activity import、search、trending、recommended feed、creator、related | 已登录扩展 tab |
| `reddit` | activity import、search、trending、community/subreddit、related | `rdt-cli` 或扩展 transport |

`CREATOR`、`COMMUNITY`、`EXPLORE` 是独立能力，避免把频道、subreddit 或跨域探索伪装为其它操作。每个平台使用独立的冻结 Pydantic settings；settings 只含开关、模式、预算与节流值，不含 Cookie、token 或其它凭据字段。

## 公开 API

| 模块 | API |
|---|---|
| `features.sources.domain` | `SourceId`, `SourceCapability`, `SourceManifest`, `SourceConnector`, `SourceTaskRequest`, `ClaimedSourceTask`, `SourceTaskCompletion` |
| `features.sources.registry` | `SourceRegistry`, `build_source_registry()`；函数签名显式要求七个已构造 connector，不扫描 entry point 或动态插件 |
| `features.sources.service` | `SourceTaskService.enqueue()`, `claim()`, `complete()`；`CredentialShapedPayloadError`, `StaleSourceTaskLeaseError`, `SourceTaskCompletionConflictError` |
| `infrastructure.sources.<platform>` | `<Platform>Settings`, `<Platform>Transport`, `<Platform>Connector` |
| `infrastructure.sources.browser_tasks` | `SQLAlchemyBrowserTaskRepository` |

## 规范化与身份

每个 connector 在 source package 内完成 URL、标题、作者、发布时间、内容类型与活动类型映射。`ContentItem.id` 由 `(source_id, external_id)` 生成稳定 UUID；`ActivityEvent.id` 由来源、活动类型、稳定外部 ID 与明确事件时间生成，重复 transport row 因此可幂等归一化。来源没有事件时间时不会猜测任务完成时间，而是使用明确的 epoch sentinel 并在 metadata 标记 `occurred_at_missing=true`。

## 通用浏览器任务安全

`SourceTaskRequest` 只接受七个 canonical source ID 与 `SourceCapability` operation；service 在入队前再次核对 manifest，来源未声明的操作直接拒绝。claim 只领取同一来源下最早的 pending 或 lease 已过期任务，使用随机 lease token 与条件更新避免同一任务被两个 worker 同时拥有。过期任务可以重新领取，旧 token 不得 complete。

complete 对相同 lease token + 相同结果是幂等的；不同结果得到 conflict，错误 token 或过期 token 得到 stale lease。payload/result 先做严格 JSON 验证，再递归拒绝 authorization、Cookie、password、secret、session、credential、API key 和任意 token 形状字段；异常不回显字段值。扩展在自己的登录 tab 执行任务，task payload 不承载浏览器 Cookie。

## 尚未完成

- 尚未新增 `/api/v1/source-tasks` HTTP claim/complete 路由。
- 尚未把 legacy 各平台 task endpoint 或扩展 dispatcher 改接通用合同。
- 尚未在生产 composition root 注入真实 transport；本任务只冻结服务/transport 合同并以 mock 验证。
- 尚未切换 legacy 数据或停止 v0.3 来源 producer。
