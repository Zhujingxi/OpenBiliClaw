# Provider Access（目标接入边界）

## 当前已落地

`src/openbiliclaw/access/` 已实现目标架构中匿名与手工凭据接入的 typed vertical slice，当前尚未接入生产 Composition：

- frozen `AccessRequest`、`AccessMethodDescriptor`、provider/account/permission/method value types；
- discriminated `AnonymousAccessHandle | CredentialAccessHandle`，handle 只含 provider/account scope、权限和 opaque `cred_<32 hex>` reference；
- `AccessStatus` 六态与 `VerificationResult`，失败只允许封闭的 sanitized reason，不承载 provider response body；
- `AccessMethod` protocol、typed registry 与 Core `AccessMethodRegistration` metadata；
- broker 只从 caller-supported、用户 allowed、provider-supported 且权限足够的方法中按请求顺序选择；
- anonymous method 只允许 `read_public`，live probe 会把限流、地域限制和网络不可用映射成安全状态，绝不虚构 account identity；
- provider-owned `ConnectionForm`、字段 shape/长度验证与 `ManualProviderSpec`，中央 Access 模块没有 provider switch；
- manual submission 校验后直接写 `CredentialVault`，只留下 opaque handle；replace 复用 reference 并增加 revision，disconnect 删除 vault secret；
- `AccessService` 提供 connect/status/replace/disconnect，成功验证缓存同时受 5 分钟默认 TTL 和 provider expiry 限制，credential replacement 必定重新验证；
- telemetry 只记录 operation/provider/outcome，异常类型和状态均不含 submission value。

`access/` 只依赖 Core extension metadata、Infrastructure CredentialVault/telemetry 与标准库/Pydantic。AST gate 禁止它导入 product/host/provider 模块，并禁止 model-visible `ai/`/未来 `assistant/` 导入 credential package。

## 公开 Python 契约

```text
AccessMethodDescriptor
AccessRequest
AccessHandle = AnonymousAccessHandle | CredentialAccessHandle
VerificationResult
AccessStatus
ConnectionForm / FormField
AccessMethod / AccessMethodRegistry
AccessBroker
AnonymousAccessMethod
ManualAccessMethod / ManualProviderSpec / CredentialVerifier
AccessService.connect | status | replace | disconnect
```

Provider auth adapter 贡献自己的 `ConnectionForm`、capabilities 与 async `CredentialVerifier`；verifier 只在 vault `resolve_async()` callback 内取得短暂只读 `memoryview`，完成或取消后 buffer 立即清零，不阻塞 event loop。原始 secret submission 不是 Pydantic model、没有 serialization API，secret field 的 ephemeral `ValidatedSubmission.__repr__` 固定 redacted。

## 状态语义

| 状态 | 含义 |
|---|---|
| `disconnected` | 无 handle |
| `unverified` | credential 无效或尚无可信成功证据 |
| `connected` | 权限满足、证据仍在 TTL/expiry 内 |
| `degraded` | scope 不足、rate limit 或仅 session-mode 能力超出当前范围 |
| `expired` | provider 明确过期或 expiry 已到 |
| `unavailable` | geo block / network unavailable |

Anonymous handle 不能带 account ID，也不能含 private-read/write permission。成功 verification 的 `granted_permissions` 少于 request 时，AccessService fail-closed 投影为 `degraded/insufficient_scope`。

## 尚未接线与批准 deviation

Plan 05 Phase 6 的 `api/source_auth/`、`auth_core.py` host cutover 和 legacy config credential reads 删除必须等 Plan 13 typed hosts；因此本阶段不新增/修改 HTTP route、CLI、TOML shape、frontend 或 production graph。旧 source-auth 仍是当前用户可见行为，新 `access/` 没有 production caller，不存在双写。

Browser-extension session、managed-browser、OAuth、CLI/browser credential import 仅保留为未来 `AccessMethod` extension categories；本阶段没有实现 browser-specific enum、payload 或 dependency。Access status 的 durable repository 也等各 owner workflow/Composition 接线时实现；当前 service 是进程内 vertical slice。
