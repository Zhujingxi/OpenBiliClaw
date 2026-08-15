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
- manual/plugin submission 校验后直接写 `CredentialVault` 的 provider/account-scoped opaque slot，只留下 opaque handle；replace 复用 reference 并增加 revision，disconnect 删除 vault secret；
- `AccessService` 提供 connect/status/replace/disconnect/rehydrate，成功验证缓存同时受 5 分钟默认 TTL 和 provider expiry 限制，credential replacement 必定重新验证；Composition startup 会幂等恢复并重新验证 vault 中的默认单账户连接，重启不再丢失连接；
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

## Plugin-assisted access (landed)

Browser-held credentials use provider-declared data and converge on the existing verified manual method after capture:

- `ProviderManifest.access_recipe` declares normalized domains, typed cookie/local-storage/session-storage artifacts, an optional declared-domain HTTPS warmup URL, and the target access method ID. It is frozen data with forbidden extra fields and no executable payload. Bilibili is the only currently declared recipe because its `builtin.manual` cookie verifier is real end to end (`SESSDATA` + `bili_jct`).
- Authenticated `GET /v1/sources/{id}/access-recipe` returns that data or typed 404. Authenticated `POST /v1/sources/{id}/access-material` requires the exact artifact identities, compiles cookie artifacts generically, validates the target method's existing form, writes only through `CredentialVault`, and invokes the same `CredentialVerifier`; malformed/missing/extra artifacts never write the vault.
- The extension stores only loopback origin plus the `openbiliclaw ext-token` value. It discovers source IDs, ignores recipe 404s, requests recipe-derived optional host permissions, reads only declared browser artifacts via generic `chrome.cookies`/`chrome.scripting` primitives, and posts them with bearer + CSRF headers. It contains no provider IDs, cookie names, or signing logic.
- Browser-executed content fetch/in-page signing (for example Douyin X-Bogus), automatic refresh scheduling, and multi-account capture remain explicitly deferred.

## Composition and exclusions

Composition supplies the credential vault, provider-owned methods, and availability refresh; Application workflows are the only host-facing entrypoint. Deleted host auth helpers and direct config credential reads have no compatibility or double-write path.

Managed-browser, OAuth, browser-executed signing/fetch, refresh scheduling, and multi-account are not implemented AccessMethods. Durable rehydration intentionally covers the local app's default account only; adding multi-account requires a separately approved durable account index. Presentation code cannot introduce provider-specific credential payloads.
