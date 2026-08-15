# OpenBiliClaw Current Architecture

OpenBiliClaw is a local-first typed content-understanding and recommendation application. Runtime Composition is the only package that knows concrete implementations.

```text
Vue web / extension ─────── generated typed client
CLI YouTube Takeout ──────── verified evidence import
              │
              ▼
       FastAPI /v1 host ─── security, CSRF/device checks, limits
              │
              ▼
      Application workflows ◄──── Assistant (optional model route)
              │             propose-only │
              ◄──── scoped pending approval
                                ▲
              models.dev live catalog + cache
                                │
                    PydanticAI native providers
                    (externally served chat + embeddings)
                                │
                    model-specific semantic index
       │       │       │       (SQLite float32 vectors)
       ▼       ▼       ▼
 Observations  Understanding  Recommendation
                               search/feed acquisition → prefilter
                               → adjacent semantic recall
                               → shadow brief compile/journal
                               → evaluation → seeded allocation
                               → constrained selection → optional visual inspection
       ▲             ▲              ▲
       └──────── Content Integration ┘
                     ▲
       first-party Content Providers
                     ▲
               Provider Access ── credentialed History/Saved ──→ Observations
                     │
 Infrastructure: SQLite/archive · vault · HTTP · events · telemetry
                     ▲
 Core: settings · lifecycle · resources · supervised jobs · health
                     ▲
             Runtime Composition
```

## Boundaries

- Hosts depend only on Application, Assistant, and Core contracts.
- Assistant receives safe application tools and bounded understanding projections; it cannot access credentials or repositories. Profile corrections are propose-only pending actions: approval dispatches canonical `EditProfile`, while the model has no direct mutation tool.
- Content providers depend on Content Integration and opaque Access handles. Optional manifest credential recipes are frozen data only; the generic browser extension reads declared artifacts, and Application routes material through the existing form/verifier/vault path. Composition rehydrates default-account opaque credential slots on startup. A 15-minute supervised job and on-demand workflow read at most two 50-item credentialed `History`/`Saved` pages and normalize them through Observation ingress; verified YouTube Takeout watch history converges on the same path; likes/subscriptions remain ignored.
- Understanding consumes immutable observations and never imports Recommendation. After canonical commits, Composition's shared AI-provider `EmbeddingIndex` best-effort projects evidence summaries and accepted claim values by opaque ID; embedding failure cannot roll back user evidence.
- Recommendation is proactive and works without Assistant. Its deterministic baseline evaluates accessible connected-provider search and bias-declared channel feed content (anonymous feeds plus credential-gated `platform-personalized` exploit supply such as Bilibili rcmd) without a model; the optional adjacent arm uses the shared model-specific semantic index to recall candidates near weak claims but below established-interest similarity. Seeded Thompson allocation and constrained selection share the append-only policy journal with post-feedback reward credit. Composition copies server-resolved exploration provenance into feedback observations and routes exploration likes/saves into Understanding's existing corroboration gate; neither domain imports the other. When a model is configured, `recommendation.brief` compiles and journals a capability/budget/privacy-validated shadow strategy before allocation. After selection, the separate `recommendation.inspect` route may inspect at most five candidates only when the catalog declares vision capability; allowlisted cover bytes flow through the existing media proxy, typed results append to the policy journal and embedding index, and every failure preserves delivery.
- Chat provider IDs, endpoints, wire protocols, and capabilities resolve from the live models.dev catalog, cached for 24 hours under the data directory with stale-cache offline fallback. PydanticAI's registry selects native implementations within the catalog protocol family. Fully custom providers must explicitly declare protocol, endpoint, and all capabilities. Embeddings retain their existing explicit external-service configuration. OpenBiliClaw does not host, bundle, or supervise model runtimes.
- Infrastructure owns resource adapters, while domain repositories remain owned by their domain packages.
- Composition builds, starts, reloads, drains, and closes the concrete graph. No product module treats `Application` as a service locator.

## Lifecycle and data safety

Startup is infrastructure → providers/services → supervised jobs → host. Shutdown is exact reverse order. Core owns every background task, timeout, resource budget, cancellation, and health record. Atomic reload validates and readies a replacement before swapping references.

The target SQLite migrator refuses unversioned application tables. Destructive migrations require explicit authorization and a verified backup; existing user data is never silently reset. The versioned local archive adapter snapshots live SQLite through its backup API, optionally carries validated/redacted config, and migrates imported snapshots forward before installation.

## Delivery

`openbiliclaw check` validates the complete graph. `openbiliclaw serve` is the sole server entrypoint used by Python packaging and Docker. Built Vue assets are served by the API host; extension packaging remains a separate build/release operation.
