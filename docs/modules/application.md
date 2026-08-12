# Application Workflows（目标模块，尚未接入生产组合根）

`src/openbiliclaw/application/` 是跨模块产品操作唯一的显式 sequencing 层。每个 workflow
接受 frozen typed command/query 和窄 Protocol 依赖；没有 command bus、workflow DSL、hook
choreography、service locator 或 god orchestrator。当前目标模块尚未由 legacy host 调用。

## 已落地边界

- model-free reads：source status、recommendation feed、profile projection、provider search、content details、job health；所有分页/数量参数有硬上限；
- mutations：connect/disconnect、typed observation import、feedback、profile edit、bounded recommendation refresh admission；外部/host mutation 强制 idempotency key；
- feedback 与 observation、profile override 与 audit observation 通过 `FeedbackObservationUnitOfWork` / `ProfileEditUnitOfWork` 明确要求同一事务；primary state commit 后才允许 adapter 发布通知；
- source connect 先由 Access 完成 credential storage + verification，再刷新 availability；刷新失败返回 recoverable result，不回滚已验证连接；
- `PendingAction` 只保存 action ID、content ref、user/account scope、安全 preview、expiry 和 idempotency key；confirmation 时重验 scope、expiry、access，成功结果幂等缓存。它不保存 credential 或任意 executable payload；
- `RefreshRecommendations` 只请求 Core-owned replenishment job admission，不调用 `asyncio.create_task`。

## Route / command / tool → workflow matrix

Plan 13/15 按此矩阵切 host；每个产品操作只有一个 owner。

`RecordFeedback` 已有 concrete SQLite UoW adapter 并通过 restart/idempotency 集成测试；`EditProfile` 的 concrete UoW adapter 随 Plan 15 composition 落地（序列与 RecordFeedback 相同：deterministic override 写入 + observation 插入，已由 typed fake 覆盖 idempotency）。

| Current surface / operation | Target workflow owner | Cutover disposition |
|---|---|---|
| HTTP source-auth status/forms/connect/disconnect | `GetSourceStatus`, `ConnectSource`, `DisconnectSource` | Plan 13 route becomes transport-only |
| CLI `profile` / profile API read | `ShowProfile` | Keep product operation |
| HTTP/CLI recommendation feed | `GetRecommendations` | Keep; model-free |
| HTTP/CLI recommendation refresh and scheduled replenishment callback | `RefreshRecommendations` | Host/scheduler submits bounded request only |
| HTTP feedback (`open/like/dislike/save/dismiss`) | `RecordFeedback` | Keep; one feedback+observation transaction |
| Host/extension observation batches and provider-history import | `RecordObservations` | Keep; browser-specific payload wrappers deleted |
| HTTP/CLI/Assistant profile edit | `EditProfile` | Keep deterministic override + audit observation |
| HTTP/CLI/Assistant content search | `SearchContent` | Keep typed provider capability call |
| HTTP/CLI/Assistant content detail | `GetContentDetails` | Keep typed provider capability call |
| Runtime/CLI job diagnostics | `GetJobHealth` | Keep payload-free health |
| Assistant/provider mutation proposal | `ProposeContentAction` | Keep pending descriptor only |
| HTTP/Assistant explicit action confirmation | `ConfirmContentAction` | Keep; revalidate access/content scope |
| Assistant conversation reads/dialogue action | Plan 12 Assistant-owned conversation facade calling the workflows above | No Application dialogue god workflow |
| legacy broad integration facade/service-location operations | none | Delete at Plan 13/15 cutover |
| legacy direct profile writes, fake source-tool dispatch, extension credential tasks | none | Delete at Plan 13/15 cutover |

## Transaction and notification rules

Workflow contracts expose validation, authorization, idempotency and audit fields. Concrete transaction
adapters are supplied by Composition. A unit-of-work returns only after primary state commits; any
post-commit publisher failure is recoverable and does not imply rollback. Cancellation is never converted
to a successful result.

## Approved deferral

Phase 11 implements plan phases 1–5. Phase 6 consumer cutover — API, CLI, runtime, Assistant and extension
rewiring plus deletion of duplicated sequencing — is deferred to Plans 13 and 15. No production caller,
double-write path, compatibility facade, route, CLI command or extension message changed in this phase.
