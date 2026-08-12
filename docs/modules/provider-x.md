# X (Twitter) Content Provider

Plan 07 的 typed first-party `x` provider 已落地于 `content/providers/x/`。

- Access: manual auth_token + ct0 cookie；secret 只经 Provider Access vault resolver 在 trusted client boundary 解析。
- Capabilities: search and fetch、purpose-specific projections；manifest 不声明 mutation/session capabilities。
- Boundary: injected transport 返回 bytes，立即校验为 provider-owned strict Pydantic models；结果与错误不携带 credential/response body。
- Legacy: `sources/` 内旧实现暂保留，直到 Plans 10–15 完成 caller cutover；production composition 尚未切换。
