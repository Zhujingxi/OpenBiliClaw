# OpenBiliClaw Current Specification

## Product

> **Vision:** OpenBiliClaw helps people get more from what they care about — whether they want to enjoy it, deepen it, connect it to adjacent ideas, or discover a new direction. The user sets the purpose; OpenBiliClaw learns across the sources they choose while keeping their discovery path from being defined by any one platform.
>
> **Invariant:** no single provider's algorithm or inferred profile may define the user's reachable discovery space (optionality). Enjoyment is a first-class intent: a comfort-only session is a successful outcome, not a failed one.

A local-first cross-platform content discovery application with deterministic learning and recommendation, optional typed AI analysis, and explicit credential boundaries.

## System architecture

```text
Responsive Vue web ─┐
Extension Vue ───────┼─ typed /v1 API ─ Application workflows ─ product modules
 recipe-driven grab ─┘         └─ provider form/verifier ─ opaque vault slot
CLI workflows/export/import ─┘               │               ▲
YouTube Takeout ────────────→ external evidence observations  │
Assistant correction ─ propose → pending approval → EditProfile ┘
                                             ▼               │
                       composition → lifecycle/supervisor    │
                                  │                          │
 credentialed history/saves → observations → understanding
              provider feeds → adjacent recall → shadow brief → seeded allocation → constrained selection
                                                                          → optional visual inspection
                                  │                         ▲
                     SQLite semantic index ←── PydanticAI native embedding provider
                                  │                         │
                           infrastructure        configured chat providers
```

## Required behavior

1. Start without credentials or a model and expose health, sources, profiles, and an empty feed.
2. Connect anonymous or provider-owned manual-secret access without exposing submitted secrets.
3. Record typed observations and feedback idempotently; bounded credentialed history/saves and verified YouTube Takeout watch-history exports use the same deterministic external-evidence normalization and existing Understanding analyzer, with authenticated behavior capped below explicit-statement trust.
4. Discover connected-provider search and feed content, hard-prefilter it, recall adjacent supply from model-specific evidence/claim/candidate embeddings, optionally compile/journal a validated RecommendationBrief in shadow mode, run seeded/journaled allocation, and build a diversity-constrained slate through the one recommendation pipeline. After durable selection, a separate vision-required per-candidate route may cache visual judgments for at most five shortlisted items and upsert their summaries for future consumers; it never enters the batched evaluator or gates delivery. Normal feeds do not depend on Assistant; missing models, frames, briefs, embeddings, or exploration supply never block delivery.
5. Search/fetch through provider-native capability contracts and opaque access handles.
6. Require CSRF/device proof for HTTP mutations. Content actions use propose/confirm, expiry, replay protection, and revalidation; Assistant profile corrections are also propose-only pending actions and confirmation alone dispatches canonical `EditProfile`.
7. Run configured Assistant/model work only through typed AI Runtime routes and budgets. Native streamed runs preserve cancellation/resource bounds, expose no tool arguments/results, and never retry after visible output.
8. Validate/build/ready/swap/drain atomically; cancellation and shutdown leave no owned task or resource open.
9. Render provider cards through shared presentation descriptors, including a safe unknown-provider fallback.
10. Access every chat and embedding model through one configuration/factory path and PydanticAI native providers; never host or bundle model runtimes in OpenBiliClaw.
11. Refuse destructive or unversioned database cutover without an explicit backed-up migration/reset decision.
12. Acquire browser-held credentials only through provider-declared declarative recipes: the backend tells the extension what artifact to get and where; the extension is a generic grabber with no per-source logic, and content transport never runs in the extension.
13. Model the user without fixed trait taxonomies (no MBTI-style or soul-layer schemas): profile claims are LLM-synthesized from evidence with provenance and trust tiers — explicit user statements always outrank inference — and model-specific embeddings are the retrieval substrate for matching. Bounded vector recall is landed; LLM rerank remains a later consumer.
14. Let users correct the model's understanding through Assistant chat; corrections enter only as proposed profile revisions through the pending-action path and persist as explicit user-statement evidence, dual-written to the ledger and the embedding index.
15. Treat recommendation/exploration policy as a pluggable strategy module: the pipeline defines the strategy interface and strategies are swappable; the agentic strategy is the first implementation.
16. Agentic strategy contract: maximal semantic agency, bounded operational delegation, deterministic execution sovereignty. Per material user context the agent autonomously compiles a typed RecommendationBrief — intent, evidence-cited hypotheses with pre-registered kill conditions, query/keyword plans, inspection targets with quality rubric, slate guidance, ask/abstain, expiry. Reversible internal policy auto-applies with audit and rollback; durable user claims use proposal/corroboration; spend, privacy, credentials, and ledger mutation remain invariants below the policy layer. Learned statistics own per-feed magnitudes; user evidence and the agent policy journal are separate append-only planes; there are no unexplained global recommendation constants.
17. Exploration is intent-conditioned and self-scaling: each episode declares intent (`enjoy | accomplish | deepen | explore | uncertain`); learned allocation shrinks exploration automatically when the user reveals low appetite for it, but only the explicit `exploration.disabled=true` user statement may zero it. Without that statement, keeping some discovery space reachable is the optionality invariant (constitution, not policy); passive disengagement can train an arm but can never disable exploration.
18. Treat provider recommendation streams as first-class acquisition channels: provider manifests declare a channel registry where each feed records its bias (`platform-popularity | platform-personalized | subscription-graph | editorial`) and auth requirement; candidates carry channel provenance and per-channel yield is learned per user. Platform-personalized channels are exploit-class supply only — exploration supply comes from cross-provider channels or our own hypothesis channels, never from the same platform's model of the user. **Landed:** cookie-authenticated Bilibili `rcmd` joins bounded replenishment only for credential handles, retains `bilibili:rcmd` exploit provenance, and credits feedback to a channel-namespaced hypothesis/Beta posterior; channel-volume bandit allocation remains deferred while acquisition round-robins available channels.
19. CLI covers product functionality as thin pass-through commands over Application workflows — same contracts as the API host, no business logic in commands. For agents it is a thin JSON request/answer pipe; agent ecosystems use CLI plus skills, never MCP or an exposed OpenAPI surface. **Landed:** sources/forms/recipes/material/connect/disconnect/sync, feed/refresh/feedback/observations, profile reads/edits/exploration, Assistant/conversation reads, search/detail/pending actions, runtime/events/diagnostics, model catalog/configuration, setup tokens/passwords, and archive/provider-evidence import/export.
20. Hosts authenticate with password login for local clients and generated tokens for extension/plugin and agent access. **Landed:** PBKDF2 login, session/extension bearer tokens, CLI setup, HTTP/WebSocket enforcement, and the guarded Vue login flow with local session-token persistence. Provider images are served through a local image proxy so provider CDNs never see user requests.
21. Provide versioned export/import of user data (SQLite + config) as the local-first ownership and backup path. **Landed:** format-v1 local archives use a consistent SQLite backup snapshot, compatibility/table-count manifest, optional redacted config, non-empty-target guard, and migration-forward restore.
22. Proactive delivery (push notifications: "found things worth your time") is a wanted product channel; its machinery is justified by the vision, not engagement metrics.

## Current capability limits

Some landed provider packages have validated projections/contracts but no production HTTP transport and therefore fail closed on live calls. Pending actions are persisted in the target SQLite schema. These limits are surfaced as unavailable capability responses; no legacy implementation is retained.
