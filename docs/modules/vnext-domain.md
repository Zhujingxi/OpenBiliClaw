# vNext 领域契约

## 状态与边界

本模块是权威 vNext 后端的领域层，只定义无框架依赖的不可变契约与纯策略。相邻的 [vNext 持久化模块](vnext-persistence.md) 提供 SQLAlchemy/Alembic adapter，[vNext 用例与后台任务](vnext-use-cases-jobs.md) 在 API 与独立 worker 中组合这些类型；公开请求只经 `/api/v1`，功能型 legacy CLI 已删除。领域模块本身仍不得导入 FastAPI、SQLAlchemy、Huey、PydanticAI、legacy Soul 或 legacy storage。Web/extension generated clients 只消费这些合同，不反向成为领域依赖。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 活动证据 | ✅ | `ActivityEvent` 统一来源事件；`ProfileSignal` 强制至少一条 evidence |
| 证据画像 | ✅ | `ProfileSnapshot` / `ProfileDelta` 冻结修订输入；`ProfileEdit` 明确表达 narrative、五类 facet upsert/removal 与 optimistic expected revision；`apply_profile_delta()` 保护用户覆盖并合并重复 facet；application 层另以 expected base revision 与独立 consumed ledger 保护并发/重复投影 |
| 候选与 Feed | ✅ | `ContentItem`、`CandidateAssessment`、`FeedEntry`、`Interaction` 统一内容和反馈边界 |
| 本地收藏 | ✅ | `CollectionItem` 表达 favorites / watch-later 本地成员关系；`LibraryItem` 将它与可渲染 `ContentItem` 组合 |
| 持久化聊天 | ✅ | `ChatTurn` 表达 user / assistant 对话轮次；`ChatHistoryTurn` 是不含 AI run/provider metadata 的公开投影 |
| 来源能力 | ✅ | `SourceId` / `SourceManifest` 分开声明 capability 与 concrete operation，并附 Pydantic-derived settings/credential/request/result schema；`SourceConnector` 只返回规范化活动或内容对象 |
| 来源任务合同 | ✅ | 七种 discriminated browser request/result、`SourceTaskRequest`、`ClaimedSourceTask`、`SourceTaskSnapshot`、`SourceTaskCompletion` 冻结通用任务边界；claim/snapshot 暴露 durable request deadline 与 `cancelled/abandoned` 终态；`SourceAccountDisconnectResult` 表达 secret-free idempotent disconnect |
| 相邻持久化与 worker adapter | ✅ | vNext SQLAlchemy/Alembic 基础和四任务 worker 已实现，但不属于本领域模块；`/api/v1` 公开请求与 worker 共用这些合同 |
| 运行时接线 | ✅ | AI、来源 adapter、application use case、`/api/v1`、运维 CLI、独立 worker 与现有 Web/extension clients 已切换 |

## 公开 API

| 模块 | 契约 / 策略 |
|------|-------------|
| `features.activity.domain` | `ActivityKind`, `ActivityEvent`, `ProfileSignal` |
| `features.profile.domain` | `FacetName`, `ProfileFacet`, `ProfileFacetEdit`, `ProfileFacetReference`, `ProfileEdit`, `ProfileSnapshot`, `ProfileDelta`, `apply_profile_delta()` |
| `features.feed.domain` | `ContentItem`, `CandidateAssessment`, `FeedEntry`, `InteractionKind`, `Interaction`, `feed_deficit()` |
| `features.library.domain` | `CollectionKind`, `CollectionItem`, `LibraryItem` |
| `features.chat.domain` | `ChatRole`, `ChatTurn`, `ChatHistoryTurn` |
| `features.sources.domain` | `SourceId`, `SourceCapability`, `SourceOperation`, `SourceOperationSpec`（primary + optional fallback transport + request/result schema）, `SourceManifest`（settings/credential schema）, `SourceConnector`, seven typed browser request/result variants, source-account status/disconnect, task claim/status/snapshot/completion models |

所有 Pydantic 契约均使用 `frozen=True` 与 `extra="forbid"`，支持 JSON 序列化后由同类型无损还原。`ActivityEvent`、`ContentItem` 与 `Interaction` 的 metadata 只接受 JSON 值，并把对象递归冻结为只读 mapping、数组递归冻结为 tuple；序列化时还原为普通 JSON object/array。`SourceConnector` 是 runtime-checkable Protocol，不是 transport payload 容器，其 normalized result annotations 可在运行时解析。七平台实现、能力矩阵和通用任务安全合同见 [vNext 多来源连接器与通用浏览器任务](vnext-sources.md)。

## 确定性策略

`apply_profile_delta()` 以 `(facet name, value.casefold())` 识别同一 facet，按 confidence 加权合并普通 facet 的 weight，并在普通/覆盖合并的两个方向都保留稳定去重后的全部 evidence。用户覆盖自动获得 `confidence=1.0`，其 value、weight 与覆盖语义不被普通 delta 改写或删除。策略保留 snapshot 的稳定 ID 与创建时间，只递增 revision，因此相同输入得到相同输出。

`ProfileEdit` 对 narrative 去首尾空白，对 facet value 折叠空白并按
`(name, value.casefold())` 稳定去重；weight 必须有限并钳制到 `-1..1`，同一 facet
不能同时 upsert/remove。application service 为一次 edit 创建一条 local
`profile_override` evidence，并把所有 upsert 设为 `confidence=1` / `overridden=true`；
无论编辑字段多少，事务只生成一个 revision，revision 冲突整体回滚。新 revision 与对应
override evidence 共用 fresh aware UTC timestamp；若 wall clock 未前进，时间仍严格大于上一
revision，而不是沿用旧 snapshot 的 `created_at`。

`CandidateAssessment` 将 relevance、quality、novelty、risk 的有限数值钳制到 `0..1`，并将组合 score 再次钳制到同一区间。`feed_deficit()` 仅在 unseen 数量严格低于 low watermark 时返回补至 high watermark 所需数量；等于或高于 low watermark 时返回 `0`。
