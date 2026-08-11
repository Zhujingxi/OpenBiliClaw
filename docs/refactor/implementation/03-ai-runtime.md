# Module Plan 03: AI Runtime

## Outcome

Replace `src/openbiliclaw/llm/`'s custom completion and fake-tool stack with a typed PydanticAI execution boundary under `src/openbiliclaw/ai/runtime/`. Domain modules own agents and prompts; AI Runtime owns compatible model selection, budgets, execution policy, usage, and safe error reporting.

## Target package

```text
ai/
├── runtime/
│   ├── capabilities.py    # model requirements and advertised capabilities
│   ├── routes.py          # explicit task-to-model routing
│   ├── execution.py       # typed Agent execution wrapper
│   ├── budgets.py         # token/request/tool/time limits
│   ├── usage.py           # attribution and persistence port
│   ├── errors.py          # safe normalized failures
│   ├── history.py         # bounded history policy helpers
│   └── testing.py         # TestModel/FunctionModel fixtures
└── evaluation/
    ├── datasets.py        # generic typed dataset/run contracts
    ├── runner.py          # isolated offline execution
    └── reports.py         # typed comparisons and reports
```

## Public contracts

- `AgentId`: stable typed identity used by routing, usage, and evaluation.
- `ModelRequirements`: required tools, structured output, vision, context, streaming, and reasoning support.
- `ModelRoute`: ordered configured model instances with no silent incompatible fallback.
- `RunPolicy`: request, token, tool-call, timeout, retry, and priority limits.
- `AgentRunRequest[DepsT, OutputT]`: typed agent, dependencies, input, history, requirements, and policy.
- `AgentRunResult[OutputT]`: validated output, usage, route, timing, and safe diagnostics.
- `AIRuntime.run()`: the only production entrypoint for domain agent execution.

Do not recreate a provider-neutral `complete(prompt: str) -> str` method.

## Internal phases

### Phase 1 — PydanticAI foundation

- Add a pinned PydanticAI dependency compatible with Python 3.11–3.13.
- Build a minimal typed run wrapper around PydanticAI `Agent`, dependencies, outputs, messages, and usage limits.
- Ensure structured output validation failures are distinct from provider/network failures.
- Disable model requests by default in tests.
- Add `TestModel` and `FunctionModel` fixtures for domain modules.

### Phase 2 — Capability model and routing

- Define an explicit capability matrix for configured model instances.
- Route by stable `AgentId` and `ModelRequirements`, not arbitrary caller-name string matching.
- Validate all routes at startup; reject a configured fallback that lacks mandatory capabilities.
- Represent unavailable, rate-limited, unauthorized, timeout, invalid-output, and budget-exhausted outcomes as typed errors.
- Preserve the useful safe-classification behavior from `llm/base.py` without preserving its provider abstraction.

### Phase 3 — Budgets and concurrency

- Integrate Core resource budgets with PydanticAI usage limits and request timeouts.
- Use explicit run priorities for interactive, scheduled, evaluation, and maintenance work.
- Bound tool calls, output tokens, retries, and total elapsed time per run.
- Propagate cancellation cleanly.
- Attribute usage to agent, workflow, model instance, provider, and optional recommendation batch.

### Phase 4 — Context and history policy

- Accept only domain-owned typed dependency objects and bounded context projections.
- Keep stable system instructions separate from volatile profile/content context.
- Provide reusable history-size accounting and trimming policy primitives only. Assistant owns summarization execution, summary prompts, provenance, and persisted summary state.
- Reject oversized tool results before they enter message history.
- Add a model-message audit that proves website credentials and secret references cannot be serialized into prompts.

### Phase 5 — Domain-agent conversion support

- Define the stable agent registry used only for discovery/evaluation tooling, not runtime service location.
- Move each prompt next to its owning domain agent as that module is implemented.
- Make agent instructions, output schema, context version, and route addressable by the evaluation harness.
- Remove `complete_with_tools`, prompt-injected fake tool protocols, broad core-memory injection, and JSON-repair helpers once domain agents use typed outputs.

### Phase 6 — Evaluation harness

- Move generic offline evaluation mechanics from `eval/evaluator.py`, `loop.py`, `optimizer.py`, `report.py`, `run_logger.py`, `human_feedback.py`, and `agents.py` into `ai/evaluation/` only when a target agent or dataset still uses them.
- Define typed dataset, run, metric, comparison, and report models keyed by stable `AgentId`.
- Let domain modules own scenarios, rubrics, fixtures, and pass criteria; the harness only executes and reports them.
- Run evaluations against isolated repositories and recorded inputs so they cannot mutate production state.
- Delete generic optimizer or self-modification behavior that cannot be expressed as a reviewed instruction/configuration change.

### Phase 7 — Legacy removal

- Delete `LLMProvider`, `LLMRegistry`, `LLMService`, `SupportsComplete`, fake tool dispatch, and obsolete provider response models.
- Delete dead `agent/orchestrator.py` and `agent/skill.py`; Assistant gets native tools directly.
- Delete the old `eval/` package after generic mechanics and domain datasets have moved to their single owners.
- Retain pricing/usage data only if used by the new typed usage service.
- Prohibit imports from the deleted `llm/` and `eval/` packages with an architecture test.

## Tests and quality gates

- Route-table tests cover every required capability and fallback combination.
- Budget tests cover token, request, tool, timeout, and cancellation exhaustion.
- Agent tests use `TestModel` or deterministic `FunctionModel`; no network.
- Integration tests for real providers are marked and opt-in.
- Secret-canary tests inspect complete model messages and tool results.
- All generics preserve `DepsT` and `OutputT`; no output `cast` from `object` is allowed outside one validated boundary.

## Documentation updates during implementation

Update `docs/modules/llm.md` or replace it with an AI Runtime module document; update configuration, architecture, evaluation, and changelog docs.

## Completion criteria

- Every production model call goes through `AIRuntime.run()`.
- Every agent has a stable ID, typed dependencies, typed output, capability requirements, and run policy.
- No custom generic completion abstraction or fake tool path remains.
- Incompatible fallback is impossible at startup and runtime.
- Model-visible messages are bounded, inspectable, and secret-free.
