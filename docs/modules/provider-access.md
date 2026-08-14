# Provider Access

## 当前边界

`src/openbiliclaw/access/` 是生产组合中匿名与手工凭据接入的 typed vertical slice：

- frozen `AccessRequest`、`AccessMethodDescriptor`、provider/account/permission/method value types；
- discriminated `AnonymousAccessHandle | CredentialAccessHandle`，handle 只含 provider/account scope、权限和 opaque `cred_<32 hex>` reference；
- `AccessStatus` 六态与 `VerificationResult`，失败只允许封闭的 sanitized reason，不承载 provider response body；
- `AccessMethod` protocol、typed registry 与 Core `AccessMethodRegistration` metadata；
- broker 只从 caller-supported、用户 allowed、provider-supported 且权限足够的方法中按请求顺序选择；
- anonymous method 只允许 `read_public`，live probe 会把限流、地域限制和网络不可用映射成安全状态，绝不虚构 account identity；
- provider-owned `ConnectionForm`、字段 shape/长度验证与 `ManualProviderSpec`，中央 Access 模块没有 provider switch；
- manual submission 校验后直接写 `CredentialVault`，只留下 opaque handle；replace 复用 reference 并增加 revision，disconnect 删除 vault secret；
- `AccessService` 提供 connect/status/replace/disconnect，成功验证缓存同时受 5 分钟默认 TTL 和 provider expiry 限制，credential replacement 必定重新验证；Application 的 `ConnectSource` 只有在同一进程仍有 matching live handle 时才复用 durable idempotency result，否则重新执行 connect，避免重启后虚报 `connected`；
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
| `degraded` | scope 不足、rate limit、provider response contract 无效或仅 session-mode 能力超出当前范围 |
| `expired` | provider 明确过期或 expiry 已到 |
| `unavailable` | geo block / network unavailable |

Anonymous handle 不能带 account ID，也不能含 private-read/write permission。成功 verification 的 `granted_permissions` 少于 request 时，AccessService fail-closed 投影为 `degraded/insufficient_scope`。

## Plugin-assisted access (decided direction)

Browser-held credentials are acquired through a `plugin_assisted` AccessMethod rather than a source-aware extension:

- The provider package declares a **credential recipe** as pure data in its manifest/auth adapter: target domain, artifact list (cookie names, storage keys, headers), optional warmup URL. Recipes carry no executable payload; manifest validation rejects anything else.
- The backend serves the recipe (`GET /sources/{id}/access-recipe`) and accepts the grabbed material (`POST /sources/{id}/access-material`), which flows through the existing `CredentialVerifier` → `CredentialVault` boundary unchanged. The extension never talks to provider semantics.
- The extension authenticates to the backend with one generated token and contains zero per-source code: ask recipe → run generic primitives → post material. Adding a source never ships an extension update.
- Browser-executed content fetch (e.g. in-page request signing à la Douyin X-Bogus) is explicitly deferred: only add a proxied-fetch primitive when a real source proves cookie + backend transport insufficient.

## Composition and exclusions

Composition supplies the credential vault, provider-owned methods, and availability refresh; Application workflows are the only host-facing entrypoint. Deleted host auth helpers and direct config credential reads have no compatibility or double-write path.

Browser-extension session import is superseded by the decided `plugin_assisted` direction above. Managed-browser, OAuth, and production CLI/browser credential import are not implemented AccessMethods. The real-stack E2E helper can read a local Chrome cookie into process memory and immediately submit it through the existing manual form/verifier; this is test infrastructure, not a production AccessMethod. Access connections remain process-local: the vault persists opaque secrets but there is no provider/account-to-reference mapping, so a client must resubmit after process restart. Adding durable reconnection, managed-browser, or OAuth support requires an approved replayable typed method and the same secret-resolution boundary; presentation code cannot introduce browser-specific credential payloads.
