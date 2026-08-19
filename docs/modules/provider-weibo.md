# Weibo Content Provider

Production composition registers the typed first-party `weibo` provider from `content/providers/weibo/`.

- Access: anonymous-only visitor flow; a generated visitor `SUB` cookie lives only in memory and user cookies are never accepted or replayed (no vault resolver is wired).
- Capabilities: public search and purpose-specific projections; fetch is not advertised because anonymous detail-by-ID endpoints are login-walled upstream (2026-08); the manifest advertises no mutation/session capabilities.
- Resilience: upstream randomly soft-blocks visitor cookies (`ok=0` / `ok=1,total=0` variants); the transport bootstraps a fresh visitor cookie after 1 second of decorrelation, bounded at six attempts.
- Boundary: the injected transport returns bytes that are immediately validated as provider-owned strict Pydantic models; results and errors contain neither credentials nor response bodies.
- Deleted legacy source implementations have no compatibility surface or production caller.
