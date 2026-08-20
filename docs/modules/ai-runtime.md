# AI Runtime

`src/openbiliclaw/ai/` is the current typed PydanticAI execution boundary, provider plugins, and offline evaluation primitives. Runtime Composition constructs the configured model route. When no model is configured, related capabilities are explicitly unavailable; there is no second call stack.

## Execution boundary

`AIRuntime.run(AgentRunRequest[DepsT, OutputT]) -> AgentRunResult[OutputT]` and `AIRuntime.stream(...)` are the only production execution entrypoints. The streaming form uses PydanticAI's native `run_stream_events` and yields typed text, textual-reasoning, sanitized tool-lifecycle, and validated terminal-result events; `run()` remains the non-streaming path. A request carries a stable `AgentId`, domain-owned typed dependencies, a PydanticAI `Agent`, bounded history/context, optional PydanticAI `UserContent` attachments, `ModelRequirements`, `RunPolicy`, and usage attribution. There is no `complete(prompt) -> str` compatibility facade. Attachments are currently used only by `recommendation.inspect`; they are allowlisted-image `BinaryContent` and contain no native video.

- `ModelRoute` checks tools, structured output, vision, context, streaming, and reasoning capabilities for the primary and every fallback at construction time. Any incompatible fallback fails startup.
- `RunPolicy` maps request/input/output/total token and tool-call ceilings directly to PydanticAI `UsageLimits`, and limits total elapsed timeout, retries, and four explicit priorities. `RunPriority` is currently contract metadata only; Core `ResourceBudget` has no priority-aware queue, so priority does not yet affect admission order.
- `PolicyBook` applies per-agent RunPolicy overrides from config `[runtime.agents."<agent-id>"]` at the single `AIRuntime.run` choke point. Overrides are validated at construction, so invalid budgets fail at startup rather than at runtime. An explicit Composition reload rebuilds the runtime, but `openbiliclaw serve` has no file watcher and file edits require a process restart unless an embedder invokes that reload primitive.
- Every execution acquires Core `ResourceBudget`; timeouts become safe typed failures, while `CancelledError` propagates unchanged. Closing or cancelling a stream closes the native run and releases its slot.
- Streaming retries and fallback are allowed only before the first visible text, textual reasoning, or tool event. Tool arguments and results never enter runtime events; tool completions carry only a generic success/failure status.
- Provider failures expose only unavailable/rate-limited/unauthorized/timeout/invalid-output/budget-exhausted categories and a non-secret model instance ID, never an upstream body.
- `UsageRecord` attributes usage to agent, workflow, model instance, provider, and optional recommendation batch. `UsageSink` is only a persistence port; this phase does not pre-implement a repository.

## Context and message safety

`ContextProjection` enforces a UTF-8 byte bound at construction. `trim_history()` retains only the latest complete turns that fit the budget and performs no summarization. Assistant additionally queries the configured route's smallest context window and resolved input policy before building history, so the selected context is safe for every fallback. An oversized tool return is rejected before entering history. Input/history/context are audited before execution, and complete PydanticAI messages are audited afterward. `vault:`, Authorization, API key, password, and Cookie canaries must not enter model messages. Stable system instructions remain defined by the domain-owned `Agent`; volatile projections enter only the current user input.

## Routing and configuration status

Composition explicitly constructs `RouteTable`, `ConfiguredModel`, and the capability matrix. Credentials are not route or request fields. `ai.providers.ModelFactory` constructs a PydanticAI native provider through one configuration entrypoint, and credentials reach the selected trusted client only inside the `CredentialVault.resolve()` callback. Model hosting and local inference remain external to the application. See [AI Providers](ai-providers.md) for the complete model/embedding contract, capability probes, and safe diagnostics.

## Offline testing and evaluation

`ai.runtime.testing` exports PydanticAI `TestModel` / `FunctionModel`. Tests default globally to `ALLOW_MODEL_REQUESTS=False`; real-provider tests must be explicitly opted into and marked `integration`. `ai.evaluation` provides only immutable recorded `Dataset`, a typed runner, metrics/reports/comparison; it does not read production repositories or include optimization or self-modification.

## Domain ownership

Understanding, Recommendation, and Assistant own their stable agent identities and prompts; all execute through this runtime. Deleted `llm/`, legacy evaluation, orchestrator, and skill implementations have no compatibility wrappers. Recommendation defines the batched `recommendation.evaluate` contract separately from the per-candidate, vision-required `recommendation.inspect` route; configured `[runtime.agents."recommendation.inspect"]` limits resolve through the same `PolicyBook`. Additional offline domain evaluation datasets remain deferred.
