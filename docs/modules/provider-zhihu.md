# Zhihu Content Provider

Production composition registers the typed first-party `zhihu` provider from `content/providers/zhihu/`.

- Access: a manual `z_c0` cookie form is registered, but the production verifier probe and HTTP transport are unavailable; new connections and live reads fail closed.
- Capabilities: search, fetch, and purpose-specific projections; the manifest advertises no mutation/session capabilities.
- Boundary: a future injected transport must return bytes that are immediately validated as provider-owned strict Pydantic models; the current `_UnavailableCredentialTransport` raises before any external request. Results and errors contain neither credentials nor response bodies.
- Deleted legacy source implementations have no compatibility surface or production caller.
