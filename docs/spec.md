# OpenBiliClaw Current Specification

## Product

A local-first cross-platform content discovery application with deterministic learning and recommendation, optional typed AI analysis, and explicit credential boundaries.

## System architecture

```text
Desktop/mobile Vue ─┐
Extension Vue ───────┼─ typed /v1 API ─ Application workflows ─ product modules
CLI check/serve ─────┘                        │               ▲
                                             ▼               │
                       composition → lifecycle/supervisor    │
                                  │                          │
                           infrastructure       PydanticAI native providers
                                                        │
                                      configured chat + embedding services
```

## Required behavior

1. Start without credentials or a model and expose health, sources, profiles, and an empty feed.
2. Connect anonymous or provider-owned manual-secret access without exposing submitted secrets.
3. Record typed observations and feedback idempotently; update bounded profile projections.
4. Discover connected-provider content, hard-prefilter it, evaluate and select it through the one recommendation pipeline. Normal feeds do not depend on Assistant.
5. Search/fetch through provider-native capability contracts and opaque access handles.
6. Require CSRF/device proof for HTTP mutations. Content actions use propose/confirm, expiry, replay protection, and revalidation.
7. Run configured Assistant/model work only through typed AI Runtime routes and budgets.
8. Validate/build/ready/swap/drain atomically; cancellation and shutdown leave no owned task or resource open.
9. Render provider cards through shared presentation descriptors, including a safe unknown-provider fallback.
10. Access every chat and embedding model through one configuration/factory path and PydanticAI native providers; never host or bundle model runtimes in OpenBiliClaw.
11. Refuse destructive or unversioned database cutover without an explicit backed-up migration/reset decision.

## Current capability limits

Some landed provider packages have validated projections/contracts but no production HTTP transport and therefore fail closed on live calls. Pending actions are persisted in the target SQLite schema. These limits are surfaced as unavailable capability responses; no legacy implementation is retained.
