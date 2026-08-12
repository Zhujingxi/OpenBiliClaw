# Content Integration（目标边界，尚未接入生产组合根）

## 状态与边界

`src/openbiliclaw/content/integration/` 已实现 Refactor Plan 06 的 Phases 1–5。它是内容提供方与跨平台调用者之间的 typed contract/registry 层，不抓取站点、不做推荐策略、不读取凭据，也不把 provider-native 数据压平成万能 schema。

当前生产 discovery 仍使用 legacy `sources/protocol.py` 与现有来源实现；本模块尚无 production caller、没有双写。`SourceAdapter`、`SourceRecipe`、legacy registry/dispatcher 的替换与删除等待 Plan 07 first-party providers 和 Plan 15 composition cutover。

## 已实现功能

| 能力 | 当前契约 |
| --- | --- |
| 稳定身份 | frozen `ProviderId`、`ContentKind`、`ContentRef`；provider 负责 URL normalization，本层只验证并保存 canonical HTTP(S) URL |
| Native envelope | `NativeContent` 要求正 schema version 和已经过 provider Pydantic schema 验证的 payload；mapping/raw JSON 不能直接进入 |
| Purpose projections | 独立 `ContentPreview`、`RecommendationCandidate`、`SearchDocument`、`CardData`，均要求 source timestamp 和匹配的 native provenance |
| Read capabilities | 分离 Search / Feed / Fetch / Related / Creator / History / Saved protocol；cursor 是 provider-scoped opaque value |
| Mutation boundary | `ActionRequest` 强制 idempotency + confirmation metadata；`ActionCapability` 与 read protocols 分离 |
| Registry | Composition 显式注册；拒绝 duplicate provider 和 manifest/implementation capability mismatch；不 import-scan |
| Agent tools | PydanticAI native search/fetch tools复用相同 capability methods，先裁剪 item/title/summary budget 再进入 model history |
| Mutation tools | 只返回 `PendingActionDescriptor`，不调用 provider mutation；Plan 11 Application confirmation workflow 才能执行 |
| Provider tests | `validate_provider_contract()` 为 Plan 07 provider package 提供可复用 manifest contract check |

## Public API

- 身份与 native：`ProviderId`、`ContentKind`、`ContentRef`、`NativeContent`
- 查询与分页：`SearchQuery`、`FeedQuery`、`CreatorQuery`、`ContentFilter`、`PageRequest`、`ProviderCursor`、`ContentPage`
- capability protocols：`SearchCapability`、`FeedCapability`、`FetchCapability`、`RelatedCapability`、`CreatorCapability`、`HistoryCapability`、`SavedCapability`、`ActionCapability`、`ProjectionCapability`、`ObservationCapability`
- provider metadata：`ProviderManifest`、`NativeSchemaDescriptor`、`ActionDescriptor`、`CapabilityKind`、`ProviderAvailability`
- registration/tooling：`ContentProviderRegistry`、`build_provider_tools()`、`ToolBudget`、`PendingActionDescriptor`
- safe failures：`ContentIntegrationError` + closed `IntegrationErrorCode`

## Invariants

1. Unknown external JSON must first validate into a provider-owned Pydantic model.
2. Persisted native records carry a positive provider schema version.
3. Provider manifests are frozen and capability claims are runtime-checked at registration.
4. Cross-provider workflows consume explicit projections; providers may retain richer native models.
5. Tool metadata is generated from validated provider IDs, not provider text or response content.
6. Tool results are bounded before model history; mutation tools never execute mutations.
7. Content Integration imports neither concrete providers nor Understanding/Recommendation/Assistant/Hosts.

## 尚未实现

- first-party provider packages and their contract suites（Plan 07）
- production composition registration and provider availability health wiring（Plan 15）
- legacy `sources/` replacement/deletion（Plans 07/15）
- confirmed mutation execution（Plan 11）
- API/OpenAPI and frontend schemas（Plans 13/14）
