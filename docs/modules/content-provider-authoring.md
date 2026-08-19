# Content Provider Authoring Contract

This document defines the minimum contract for first-party provider packages using the current `content.integration` boundary. It describes implemented interfaces and does not promise runtime discovery or compatibility adapters.

## Package responsibilities

Each provider package owns:

- provider-native Pydantic payload models and schema versions;
- canonical URL normalization;
- provider API/HTML semantics, pagination cursor interpretation, and error classification;
- trusted adaptation from `AccessHandle` to provider requests;
- narrow capability implementations that are actually supported;
- native → purpose-specific projection functions;
- provider action semantics and verification;
- optional observation proposals;
- optional `ProviderManifest.access_recipe` for plugin-assisted access: normalized DNS domains, typed artifacts (`cookie | local_storage | session_storage` + domain + name), an optional same-domain HTTPS warmup URL, and an implemented access method ID. This is frozen data; executable payloads, headers, and scripts are prohibited.

Content Integration owns only shared identity, capability contracts, manifest validation, the registry, and tool budgeting. A provider must not discover or call other providers through the registry, or import Understanding, Recommendation, Assistant, or Hosts.

## Minimal registration

1. Use stable lowercase `ProviderId` and `ContentKind` values.
2. Declare `NativeSchemaDescriptor(content_kind, schema_version)` for every persisted native payload; increment the version explicitly when the schema changes.
3. Convert external JSON/HTML to a provider-owned Pydantic model before placing it in `NativeContent.payload`. Raw mappings are prohibited. When restoring persisted records, the provider must revalidate its payload model against `schema_version` before constructing `NativeContent`; the envelope itself does not accept raw dictionaries.
4. `ProviderManifest.capabilities` advertises only methods that are actually implemented. Composition registers them explicitly with `ContentProviderRegistry.register()`.
5. Declare `access_recipe` only when the target access method, form, and verifier are implemented and the material can be converted generically. Artifact identities must be unique and reference declared domains. A warmup URL must be HTTPS on a declared domain and contain no userinfo or fragment. Do not put provider-specific signing, fetch logic, refresh schedules, or credential values in a manifest; a new recipe must not require provider-specific extension code.
6. Provider tests must call:

```python
assert validate_provider_contract(manifest, provider) == ()
```

Registration rejects duplicate provider IDs, duplicate schema/action IDs, and advertised-capability mismatches.

## Production transport status

| Provider | Production wiring | Manifest capabilities | Live verification |
|----------|-------------------|-----------------------|------------------|
| Bilibili | anonymous public reads; verified manual/plugin cookies (`SESSDATA` + `bili_jct`) | Search, Fetch, Related, Creator, Projection, anonymous `popular`, credential-only `rcmd`, History, Saved, Observation, Action | `l1a` anonymous and `l1b` authenticated; personalized-feed/ingestion coverage is hermetic |
| YouTube | anonymous `yt-dlp` transport; no API key or cookie | Search, Fetch, Creator, Projection; no generic Feed | `l1youtube` |
| Bangumi | anonymous HTTP transport; PAT form retained but its production identity verifier is fail-closed unavailable | Search, Feed (`rank` channel), Fetch, Projection | `l1bangumi` |
| V2EX | anonymous HTTP transport; PAT form retained but its production identity verifier is fail-closed unavailable | Feed (`hot` channel), Fetch, Creator, Projection; no Search | `l1v2ex` |
| Hacker News | anonymous official Firebase transport | Feed (`top` channel), Fetch, Projection; no Search | hermetic transport/provider coverage; no live layer |
| Weibo | anonymous visitor `SUB` generated in memory; user cookies are not accepted | Search, Projection; no Fetch | `l1weibo` |
| Linux.do | anonymous Discourse JSON transport; manual cookie verifier is unavailable | Search, Fetch, Projection. The package is enabled by the example config, but Cloudflare may reject non-browser clients | no live layer; upstream-blocked on the verified host |
| Reddit | manual form registered; production verifier and HTTP transport unavailable | Search, Fetch, Projection contracts register, then live calls fail closed | hermetic contract coverage only |
| X | manual form registered; production verifier and HTTP transport unavailable | Search, Fetch, Projection contracts register, then live calls fail closed | hermetic contract coverage only |
| Zhihu | manual form registered; production verifier and HTTP transport unavailable | Search, Fetch, Projection contracts register, then live calls fail closed | hermetic contract coverage only |
| RedNote | no live read path or production transport | Projection only; manifest availability is `degraded` | hermetic schema/projection coverage only |
| Douyin | no live read path or production transport | Projection only; manifest availability is `degraded` | hermetic schema/projection coverage only |

## Capability rules

- Implement the smallest protocol; do not build a provider god interface.
- When providing a recommendation/feed capability, the manifest must declare each `feed_id`'s bias class (`platform-popularity | platform-personalized | subscription-graph | editorial`) and authentication requirement. Anonymous and credentialed feeds must not be silently conflated. **Implemented and enforced by `ProviderManifest`**: FEED without a channel and duplicate `feed_id` values fail before startup registration.
- If a projection can produce `CardData.image_url`, the manifest must declare only the real CDN DNS allowlist in `image_hosts` (exact hostnames; declare a parent domain only when dynamic prefixes require it) and only static `image_headers` truly required by that CDN (pure data, such as Bilibili Referer). Schemes/paths, IP literals, trailing dots, non-canonical case, executable header logic, or payload-expanded allowlists are prohibited. The host proxies only HTTPS `image/*`, never video/audio.
- `ProviderCursor.value` is fully opaque; only the provider that created it may parse it. Credentialed evidence ingestion may follow it only within a hard two-page bound.
- Read methods accept a scoped `AccessHandle` and must not expand provider/account/permission scope.
- Limits in `SearchQuery`, `FeedQuery`, `CreatorQuery`, and `PageRequest` are hard ceilings.
- Provider-specific failures may be normalized at the integration boundary only to unavailable capability, invalid content ref, access denied, rate limited, or provider unavailable; they must not contain response bodies, cookies, or tokens.
- `ActionCapability` executes only an `ActionRequest` already confirmed by Application. The request must include an idempotency key and confirmation metadata.

## Projection rules

Providers generate projections according to purpose:

- `ContentPreview`: small read/tool result;
- `RecommendationCandidate`: discovery provenance only, with no presentation fields;
- `SearchDocument`: indexable text only, with no badge/image presentation fields;
- `CardData`: presentation data, with no recommendation reason/score.

Every projection must carry an aware `source_timestamp` and `ProjectionProvenance`; the provenance ref must match the projection ref.

## Native tool rules

Provider capabilities are invoked by Application Workflows and Assistant workflow tools rather than direct provider-generated model tools. Direct provider-generated tools were deleted; capability exposure is now controlled by the workflow selection layer:

- Search/fetch workflows directly call the same provider capabilities used by deterministic recommendation jobs.
- Provider display text and response text never become tool metadata.
- Assistant workflow results are bounded before entering model history.
- Mutations are proposed as pending actions and never execute inside an Assistant tool.

## Required provider tests

- manifest/implementation contract validation;
- rejection of malformed/unknown JSON for each native schema;
- schema-version and canonical-reference stability;
- cursor/provider scope and page limits;
- projection provenance/timestamp and API serialization;
- malformed, missing, expired, and insufficient-scope access cases;
- bounded Assistant workflow results and secret-canary inspection;
- normalized failures contain no raw provider body or credential.
