# LinuxDo Content Provider

Production composition registers the typed first-party `linuxdo` provider from `content/providers/linuxdo/`.

- Access: anonymous public reads are wired. A manual `_t` cookie form is retained, but its production verifier probe is unavailable and new manual connections fail closed.
- Capabilities: search, fetch, and purpose-specific projections; the manifest advertises no mutation/session capabilities. `config.example.toml` enables the package, but Linux.do may still return an access-denied typed failure when Cloudflare challenges non-browser clients.
- Boundary: the injected transport returns bytes that are immediately validated as provider-owned strict Pydantic models; results and errors contain neither credentials nor response bodies.
- Deleted legacy source implementations have no compatibility surface or production caller.
