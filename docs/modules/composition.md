# Runtime Composition

## Status

Runtime Composition is the only production assembly and process entrypoint. `openbiliclaw check` validates, starts, checks readiness, and shuts down the graph; `openbiliclaw serve` runs the composed FastAPI host.

## Production graph

`composition.build.build_application()` constructs a frozen `Application` with:

- validated `core.AppSettings`;
- SQLite schema/repositories, credential vault, HTTP clients, events, telemetry;
- all landed first-party provider packages (Bilibili, YouTube, Bangumi, V2EX, Reddit, X, Zhihu, LinuxDo, Weibo, RedNote, Douyin);
- anonymous and provider-owned manual-secret access methods;
- observation, understanding, deterministic recommendation and application workflows;
- one model-specific `EmbeddingIndex` shared through narrow post-commit writer and semantic-recall boundaries; unconfigured mode is a no-op and provider failures are contained;
- one policy journal/hypothesis registry shared by replenishment allocation and post-feedback reward credit;
- Core-owned recommendation jobs with resource budgets, timeout, cancellation and health;
- one FastAPI host and the built Vue frontend.

Startup order is infrastructure → services → Core jobs; shutdown reverses it. The composition reload primitive builds and readies a candidate before swapping, drains the old graph to a deadline, then closes it. Failed candidates never replace the active graph. No automatic file watcher is installed; embedders may explicitly invoke this primitive after validating replacement settings.

## Current reduced baseline

The final cutover deliberately does not preserve legacy-only behavior. Provider packages without a landed production HTTP transport register their validated contracts but fail closed on live calls. Model-free recommendation replenishment combines connected search with connected anonymous feed channels, optionally adds embedding-backed adjacent supply, runs a seeded/journaled uncertain-intent allocation, and sends any winning exploration attribution into constrained selection; Assistant turns use a configured model route. Pending actions use the target SQLite repository and survive restart. These are explicit capability limits, not compatibility shims.

## Public API

- `build_application(settings, options=...) -> Application`
- `validated_settings(path, environ=..., overrides=...) -> AppSettings`
- `ApplicationReference.lease()/swap()/drain()`
- console command: `openbiliclaw {check,serve}`
