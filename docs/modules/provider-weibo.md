# Weibo Content Provider

Production composition registers the typed first-party `weibo` provider from `content/providers/weibo/`.

- Access: anonymous-only visitor flow; a generated visitor `SUB` cookie lives only in memory and user cookies are never accepted or replayed (no vault resolver wired).
- Capabilities: public search、purpose-specific projections；fetch 不声明（anonymous detail-by-id endpoints are login-walled upstream, 2026-08）；manifest 不声明 mutation/session capabilities。
- Resilience: upstream randomly soft-blocks visitor cookies (ok=0 / ok=1-total=0 variants)；transport re-bootstraps a fresh visitor cookie with 1s decorrelation, bounded at six attempts.
- Boundary: injected transport 返回 bytes，立即校验为 provider-owned strict Pydantic models；结果与错误不携带 credential/response body。
- Deleted legacy source implementations have no compatibility surface or production caller.
