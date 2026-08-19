# Reddit Content Provider

Production composition registers the typed first-party `reddit` provider from `content/providers/reddit/`.

- Access: a manual Reddit session-cookie form is registered, but the production verifier probe and HTTP transport are unavailable; new connections and live reads fail closed.
- Capabilities: search, fetch, and purpose-specific projections; the manifest advertises no mutation/session capabilities.
- Boundary: a future injected transport must return bytes that are immediately validated as provider-owned strict Pydantic models; the current `_UnavailableCredentialTransport` raises before any external request. Results and errors contain neither credentials nor response bodies.
- Deleted legacy source implementations have no compatibility surface or production caller.
