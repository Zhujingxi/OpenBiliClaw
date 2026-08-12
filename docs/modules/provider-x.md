# X (Twitter) Content Provider

Production composition registers the typed first-party `x` provider from `content/providers/x/`.

- Access: manual auth_token + ct0 cookie；secret 只经 Provider Access vault resolver 在 trusted client boundary 解析。
- Capabilities: search and fetch、purpose-specific projections；manifest 不声明 mutation/session capabilities。
- Boundary: injected transport 返回 bytes，立即校验为 provider-owned strict Pydantic models；结果与错误不携带 credential/response body。
- Deleted legacy source implementations have no compatibility surface or production caller.
