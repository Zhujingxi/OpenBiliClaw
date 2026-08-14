# Content Provider Authoring Contract

本文说明 first-party provider package 使用当前 `content.integration` 边界的最小契约。它描述已落地接口，不承诺 runtime discovery 或 compatibility adapters。

## Package responsibilities

每个 provider package 自己拥有：

- provider-native Pydantic payload models 与 schema version；
- canonical URL normalization；
- provider API/HTML semantics、pagination cursor interpretation 和错误分类；
- AccessHandle 到 provider request 的可信适配；
- 实际支持的 narrow capability implementations；
- native → purpose-specific projection functions；
- provider action semantics 与 verification；
- 可选 observation proposal。

Content Integration 只拥有共享 identity、capability contracts、manifest validation、registry 和 tool budgeting。Provider 不得从 registry 发现/调用其他 providers，也不得 import Understanding、Recommendation、Assistant 或 Hosts。

## Minimal registration

1. 使用稳定 lowercase `ProviderId` 和 `ContentKind`。
2. 为每种 persisted native payload 声明 `NativeSchemaDescriptor(content_kind, schema_version)`；schema 变化时显式递增版本。
3. 将外部 JSON/HTML 先转换为 provider-owned Pydantic model，再放入 `NativeContent.payload`。禁止 raw mapping。持久化记录恢复时，provider 需按 `schema_version` 重新校验自己的 payload model 后再构造 `NativeContent` —— envelope 自身不接受 raw dict。
4. `ProviderManifest.capabilities` 只声明真实实现的方法；Composition 以 `ContentProviderRegistry.register()` 显式注册。
5. Provider tests 必须调用：

```python
assert validate_provider_contract(manifest, provider) == ()
```

注册会拒绝 duplicate provider ID、重复 schema/action ID 与 advertised capability mismatch。

## Production transport status

| Provider | Enabled access | Capabilities | Live E2E layer |
|----------|----------------|--------------|----------------|
| Bangumi | `builtin.anonymous` public read; optional PAT form retained | search, feed, fetch, projection via official `api.bgm.tv` v0 API | `l1bangumi` (anonymous search → detail) |
| V2EX | `builtin.anonymous` public read; optional PAT form retained | feed, fetch, creator, projection via official `www.v2ex.com/api` endpoints; search not advertised because V2EX has no official full-text search API | `l1v2ex` (anonymous hot feed → detail) |

## Capability rules

- 实现最小 protocol，不建立 provider god interface。
- `ProviderCursor.value` 完全 opaque；只有创建它的 provider 可以解析。
- read methods 接收 scoped `AccessHandle`，不得扩大 provider/account/permission scope。
- `SearchQuery` / `FeedQuery` / `CreatorQuery` / `PageRequest` 的 limit 是硬上限。
- provider-specific failures 只能在 integration boundary 归一为：unavailable capability、invalid content ref、access denied、rate limited、provider unavailable；不得携带 response body、cookie 或 token。
- `ActionCapability` 只执行已经由 Application 确认的 `ActionRequest`，请求必须包含 idempotency key 与 confirmation metadata。

## Projection rules

Provider 根据调用目的分别生成：

- `ContentPreview`：小型读取/tool result；
- `RecommendationCandidate`：只含 discovery provenance，不含 presentation fields；
- `SearchDocument`：只含可索引文本，不含 badge/image 等 presentation fields；
- `CardData`：presentation data，不含 recommendation reason/score。

每个 projection 必须带 aware `source_timestamp` 和 `ProjectionProvenance`；provenance ref 必须与 projection ref 一致。

## Native tool rules

Provider capabilities are invoked by Application Workflows and Assistant workflow tools rather than direct provider-generated model tools. Direct provider-generated tools were deleted; capability exposure is now controlled by the workflow selection layer:

- Search/fetch workflows directly call the same provider capabilities used by deterministic recommendation jobs.
- Provider display text and response text never become tool metadata.
- Assistant workflow results are bounded before entering model history.
- Mutations are proposed as pending actions and never execute inside an Assistant tool.

## Required provider tests

- manifest/implementation contract validation；
- each native schema malformed/unknown JSON rejection；
- schema-version and canonical-reference stability；
- cursor/provider scope and page limit；
- projection provenance/timestamp and API serialization；
- malformed, missing, expired and insufficient-scope access cases；
- bounded Assistant workflow result and secret-canary inspection；
- normalized failures contain no raw provider body or credential。
