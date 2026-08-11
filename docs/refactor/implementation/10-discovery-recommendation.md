# Module Plan 10: Discovery & Recommendation

## Outcome

Consolidate current discovery, producer, candidate-evaluation, and recommendation engines into one explicit pipeline under `src/openbiliclaw/recommendation/`. It owns candidate acquisition through final recommendation history while keeping ranking correctness deterministic.

## Target package

```text
recommendation/
├── models.py              # candidate, evaluation, inventory, recommendation
├── discovery/
│   ├── strategies.py      # narrow configured strategy contracts
│   ├── query_agent.py     # typed semantic query generation
│   ├── planner.py
│   └── service.py
├── evaluation/
│   ├── prefilter.py
│   ├── agent.py
│   └── service.py
├── selection/
│   ├── exclusions.py
│   ├── ranking.py
│   ├── diversity.py
│   └── service.py
├── expression/
│   ├── agent.py
│   └── service.py
├── feedback.py
├── service.py
├── jobs.py
└── repositories.py
```

## Pipeline states

Define a typed candidate state machine, for example:

```text
discovered → normalized → prefiltered → evaluated → admitted
          ↘ rejected
admitted → selected → shown → interacted/expired
```

Transitions are explicit and persisted. Do not infer state from nullable columns.

## Internal phases

### Phase 1 — Domain model and persistence contracts

- Define candidate identity using `ContentRef` plus strategy/query provenance.
- Define typed evaluation, rejection, admission, selection, shown-history, and feedback records.
- Put uniqueness and valid-transition constraints in deterministic code and SQLite.
- Define repository ports by aggregate: inventory, evaluations, recommendations, shown history, feedback.
- Add state-transition and idempotency tests.

### Phase 2 — Discovery strategies, query agent, and planning

- Convert proven search, trending, related-chain, exploration, direct-provider, and inspiration behavior into narrow `DiscoveryStrategy` implementations.
- Add the stable `recommendation.query` PydanticAI agent with typed profile/topic input and bounded provider-neutral query suggestions as output.
- Let the deterministic planner decide whether query generation is needed, validate/deduplicate generated queries, map them to compatible provider capabilities, and enforce provider/inventory quotas.
- Strategies return bounded provider queries or candidates; they do not persist or rank.
- Planner uses `DiscoveryProfile`, provider availability, inventory pressure, and configured quotas; deterministic/default queries keep discovery functional when the model is unavailable.
- Background discovery calls typed provider APIs directly, never agent tools.
- Remove platform producer classes whose only role was scheduling one provider call.

### Phase 3 — Deterministic normalization and prefilter

- Deduplicate by provider-native identity and canonical URL where safe.
- Reject malformed, blocked, already-seen, stale, inaccessible, or unsupported candidates before model calls.
- Apply hard user avoidances deterministically.
- Record rejection reasons for audit and tuning.
- Batch embedding similarity or lightweight heuristics only when configured and provenance-compatible.

### Phase 4 — One-shot evaluation agent

- Build a stable PydanticAI evaluation agent with typed batch input/output.
- Supply compact `RecommendationProfile`, candidate previews, and explicit scoring rubric.
- Bound candidates, text, images, tokens, retries, and total execution time.
- Validate one result per candidate and reject missing/duplicate IDs.
- Persist model identity, rubric version, context version, usage, score, rationale, and uncertainty.
- Model failure leaves candidates pending/retryable; it never bypasses hard rules.

### Phase 5 — Admission and deterministic selection

- Admit only evaluated candidates meeting explicit thresholds and capacity rules.
- Enforce seen filtering, negative preferences, freshness, provider/source quotas, creator repetition, topic diversity, and inventory expiry.
- Implement ranking as named deterministic components with inspectable score contributions.
- Keep selection deterministic for a fixed inventory/profile/configuration/seed.
- Add table/golden tests for edge cases and fairness across providers.

### Phase 6 — Expression

- Make expression optional and downstream of final selection.
- Use a typed agent to generate concise recommendation reasons/tone without changing item order, IDs, actions, or scores.
- Fall back to deterministic safe copy when the model is unavailable or output validation fails.
- Store expression provenance separately from recommendation correctness.

### Phase 7 — Feedback and proactive jobs

- Record shown and interaction state transactionally through Application Workflows.
- Send typed feedback observations to Observation Ingress.
- Invalidate or reprioritize affected inventory deterministically.
- Register discovery, evaluation, expiry, and replenishment jobs through Core with explicit budgets and overlap policies.
- Make normal feed reads model-free and fast.

### Phase 8 — Domain evaluation and cleanup

- Move retained behavior from `eval/discovery_evaluator.py`, `discovery_optimizer.py`, and `discovery_scenario.py` into recommendation-owned offline scenarios and rubrics using the generic AI evaluation harness.
- Register `recommendation.query`, `recommendation.evaluate`, and `recommendation.expression` as stable evaluation identities with typed datasets, schemas, context versions, and pass criteria.
- Convert useful human-feedback fixtures to target candidate/profile schemas; Understanding owns persona/profile evaluation inputs.
- Evaluate planner recall, prefilter precision, agent scoring, selection diversity, and expression separately.
- Delete `discovery/`, old recommendation engines, pool curators, producer wrappers, candidate-eval coordinators, duplicated runtime pipelines, and superseded discovery eval files after cutover.
- Retain algorithms only when tests demonstrate target value.

## Tests and quality gates

- State-machine, deduplication, hard-exclusion, ranking, quota, diversity, expiry, and feedback tests are deterministic.
- Agent tests use fixed outputs; real-model evaluations are separate.
- Replay tests prove no duplicate recommendation or shown record after retry/restart.
- Performance tests bound selection time and database query count for realistic inventory sizes.
- MyPy types candidate states and evaluator outputs without generic dictionaries.

## Documentation updates during implementation

Replace/update discovery and recommendation module docs, runtime job docs, API schemas, evaluation docs, architecture diagrams, and `docs/changelog.md`.

## Completion criteria

- One pipeline owns discovery through recommendation history.
- Background work uses provider APIs; tools are reserved for agent-driven exploration.
- Hard exclusions, admission, ranking, diversity, persistence, and scheduling are deterministic.
- A model outage cannot corrupt inventory or prevent reading already-selected recommendations.
- Old discovery/recommendation engines and producer pipelines are deleted.
