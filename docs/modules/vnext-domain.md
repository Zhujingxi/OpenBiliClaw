# vNext 领域契约

## 状态与边界

本模块是 backend-first vNext 的第一层基础，只定义无框架依赖的不可变领域契约与纯策略；相邻的 [vNext 持久化模块](vnext-persistence.md) 已提供 SQLAlchemy/Alembic adapter，[vNext 用例与后台任务](vnext-use-cases-jobs.md) 已在独立 worker 中组合这些类型，但当前 legacy runtime、存储、CLI 和公开 API尚未切换。领域模块本身仍不得导入 FastAPI、SQLAlchemy、Huey、PydanticAI、legacy Soul 或 legacy storage。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 活动证据 | ✅ | `ActivityEvent` 统一来源事件；`ProfileSignal` 强制至少一条 evidence |
| 证据画像 | ✅ | `ProfileSnapshot` / `ProfileDelta` 冻结修订输入；`apply_profile_delta()` 保护用户覆盖并合并重复 facet |
| 候选与 Feed | ✅ | `ContentItem`、`CandidateAssessment`、`FeedEntry`、`Interaction` 统一内容和反馈边界 |
| 本地收藏 | ✅ | `CollectionItem` 只表达 favorites / watch-later 本地集合成员关系 |
| 持久化聊天 | ✅ | `ChatTurn` 表达 user / assistant 对话轮次 |
| 来源能力 | ✅ | `SourceId` / `SourceManifest` 分开声明稳定 capability 与带 auth/result/transport metadata 的 concrete operation；`SourceConnector` 只返回规范化活动或内容对象 |
| 来源任务合同 | ✅ | `SourceTaskRequest`、`ClaimedSourceTask`、`SourceTaskSnapshot`、`SourceTaskCompletion` 冻结通用任务边界；claim/snapshot 暴露 durable request deadline 与 `cancelled/abandoned` 终态，lease 策略见相邻 vNext 来源模块 |
| 相邻持久化与 worker adapter | ✅ | 独立 vNext SQLAlchemy/Alembic 基础和四任务 worker 已实现，但不属于本领域模块；公开请求路径仍未切换 |
| 运行时接线 | 🚧 | AI 与来源 adapter 基础已实现；use case、API、数据迁移与现有前端切换由后续任务实现 |

## 公开 API

| 模块 | 契约 / 策略 |
|------|-------------|
| `features.activity.domain` | `ActivityKind`, `ActivityEvent`, `ProfileSignal` |
| `features.profile.domain` | `FacetName`, `ProfileFacet`, `ProfileSnapshot`, `ProfileDelta`, `apply_profile_delta()` |
| `features.feed.domain` | `ContentItem`, `CandidateAssessment`, `FeedEntry`, `InteractionKind`, `Interaction`, `feed_deficit()` |
| `features.library.domain` | `CollectionKind`, `CollectionItem` |
| `features.chat.domain` | `ChatRole`, `ChatTurn` |
| `features.sources.domain` | `SourceId`, `SourceCapability`, `SourceOperation`, `SourceOperationSpec`（primary + optional fallback transport）, `SourceManifest`, `SourceConnector`, browser task request/claim/status/snapshot/completion models；claim/snapshot 含 `request_deadline_at`，status 含 `cancelled/abandoned` |

所有 Pydantic 契约均使用 `frozen=True` 与 `extra="forbid"`，支持 JSON 序列化后由同类型无损还原。`ActivityEvent`、`ContentItem` 与 `Interaction` 的 metadata 只接受 JSON 值，并把对象递归冻结为只读 mapping、数组递归冻结为 tuple；序列化时还原为普通 JSON object/array。`SourceConnector` 是 runtime-checkable Protocol，不是 transport payload 容器，其 normalized result annotations 可在运行时解析。七平台实现、能力矩阵和通用任务安全合同见 [vNext 多来源连接器与通用浏览器任务](vnext-sources.md)。

## 确定性策略

`apply_profile_delta()` 以 `(facet name, value.casefold())` 识别同一 facet，按 confidence 加权合并普通 facet 的 weight，并在普通/覆盖合并的两个方向都保留稳定去重后的全部 evidence。用户覆盖自动获得 `confidence=1.0`，其 value、weight 与覆盖语义不被普通 delta 改写或删除。策略保留 snapshot 的稳定 ID 与创建时间，只递增 revision，因此相同输入得到相同输出。

`CandidateAssessment` 将 relevance、quality、novelty、risk 的有限数值钳制到 `0..1`，并将组合 score 再次钳制到同一区间。`feed_deficit()` 仅在 unseen 数量严格低于 low watermark 时返回补至 high watermark 所需数量；等于或高于 low watermark 时返回 `0`。
