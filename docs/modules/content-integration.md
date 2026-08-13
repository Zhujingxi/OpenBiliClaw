# Content Integration

`src/openbiliclaw/content/integration/` is the typed contract and registry boundary between content providers and cross-platform callers. It does not fetch websites, rank recommendations, read credentials, or flatten provider-native payloads into a universal raw schema. Production composition explicitly registers the first-party providers here; import scanning and legacy source adapters do not exist.

## Contract

| Capability | Current contract |
| --- | --- |
| Stable identity | frozen `ProviderId`, `ContentKind`, `ContentRef`; providers normalize canonical HTTP(S) URLs |
| Native envelope | `NativeContent` requires a positive schema version and provider-owned Pydantic validation |
| Purpose projections | separate `ContentPreview`, `RecommendationCandidate`, `SearchDocument`, and `CardData` |
| Reads | distinct Search, Feed, Fetch, Related, Creator, History, and Saved protocols; cursors are provider-scoped opaque values |
| Mutations | `ActionRequest` requires idempotency and confirmation metadata; action capability is separate from reads |
| Registry | explicit composition registration; duplicate providers and capability mismatches are rejected |
| Assistant | tools reuse bounded Application workflows; provider text is untrusted and results are bounded |
| Safe failures | closed `ContentIntegrationError` / `IntegrationErrorCode` surface |

## Public API

- identity/native: `ProviderId`, `ContentKind`, `ContentRef`, `NativeContent`;
- query/paging: `SearchQuery`, `FeedQuery`, `CreatorQuery`, `ContentFilter`, `PageRequest`, `ProviderCursor`, `ContentPage`;
- capabilities: Search, Feed, Fetch, Related, Creator, History, Saved, Action, Projection, Observation;
- metadata: `ProviderManifest`, `NativeSchemaDescriptor`, `ActionDescriptor`, `CapabilityKind`, `ProviderAvailability`;
- registration: `ContentProviderRegistry`.

## Invariants

1. External JSON validates into a provider-owned Pydantic model before entering the integration boundary.
2. Persisted native records carry a positive provider schema version.
3. Manifests are frozen and capability claims are checked at registration.
4. Cross-provider workflows consume purpose-specific projections.
5. Tool metadata comes from validated provider IDs, never provider response text.
6. Tool results are bounded; mutation tools propose and Application confirmation executes.
7. Content Integration imports neither concrete providers nor Understanding, Recommendation, Assistant, or Hosts.

## Bilibili native boundary

The Bilibili client accepts the provider's real endpoint-specific envelopes (`data.list` for popular feeds, `data.result` for search, and raw `data` for video details) and converts native rows into strict `BilibiliVideo` models before returning them. Search HTML markup is removed at this boundary, duration strings are normalized to seconds, and protocol-relative covers become HTTPS URLs. The transport uses browser-compatible public request headers and maps HTTP 412/429 to the typed `RATE_LIMITED` error.

Search and detail workflows are read-only. They do not populate `content_references` or `content_cache`; durable content-reference creation belongs to Observation Ingress.
