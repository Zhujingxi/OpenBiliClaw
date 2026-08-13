# OpenBiliClaw Current Architecture

OpenBiliClaw is a local-first typed content-understanding and recommendation application. Runtime Composition is the only package that knows concrete implementations.

```text
Vue web / extension ─────── generated typed client
              │
              ▼
       FastAPI /v1 host ─── security, CSRF/device checks, limits
              │
              ▼
      Application workflows ◄──── Assistant (optional model route)
                                ▲
                    PydanticAI native providers
                    (externally served chat + embeddings)
       │       │       │
       ▼       ▼       ▼
 Observations  Understanding  Recommendation
                               discovery → prefilter
                               → evaluation → selection
       ▲             ▲              ▲
       └──────── Content Integration ┘
                     ▲
       first-party Content Providers
                     ▲
               Provider Access
                     │
 Infrastructure: SQLite · vault · HTTP · events · telemetry
                     ▲
 Core: settings · lifecycle · resources · supervised jobs · health
                     ▲
             Runtime Composition
```

## Boundaries

- Hosts depend only on Application, Assistant, and Core contracts.
- Assistant receives safe application tools and bounded understanding projections; it cannot access credentials or repositories.
- Content providers depend on Content Integration and opaque Access handles.
- Understanding consumes immutable observations and never imports Recommendation. Composition exposes the configured embedding service separately; Recommendation discovery remains text-query based, so no durable semantic index is added until a concrete semantic retrieval consumer exists.
- Recommendation is proactive and works without Assistant. Its deterministic baseline evaluates accessible connected-provider content without a model; configured model routes may enrich target analyzers.
- All chat and embedding models are external services reached through one configuration/factory path and PydanticAI's native providers. OpenBiliClaw does not host, bundle, or supervise model runtimes.
- Infrastructure owns resource adapters, while domain repositories remain owned by their domain packages.
- Composition builds, starts, reloads, drains, and closes the concrete graph. No product module treats `Application` as a service locator.

## Lifecycle and data safety

Startup is infrastructure → providers/services → supervised jobs → host. Shutdown is exact reverse order. Core owns every background task, timeout, resource budget, cancellation, and health record. Atomic reload validates and readies a replacement before swapping references.

The target SQLite migrator refuses unversioned application tables. Destructive migrations require explicit authorization and a verified backup; existing user data is never silently reset.

## Delivery

`openbiliclaw check` validates the complete graph. `openbiliclaw serve` is the sole server entrypoint used by Python packaging and Docker. Built Vue assets are served by the API host; extension packaging remains a separate build/release operation.
