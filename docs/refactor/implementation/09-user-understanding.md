# Module Plan 09: User Understanding

## Outcome

Replace the overlapping `memory/` and `soul/` ownership model with `src/openbiliclaw/understanding/`, the sole writer of the canonical user profile. Durable evidence and profile state persist locally; analyzers are short-lived typed model runs that propose changes.

## Target package

```text
understanding/
├── profile.py          # canonical profile aggregate
├── evidence.py         # evidence and proposal provenance
├── ledger.py           # accepted/rejected/superseded changes
├── proposals.py        # typed analyzer outputs
├── analyzers/          # narrow PydanticAI agents
├── policy.py           # deterministic validation/conflict rules
├── service.py          # consume observations and commit
├── projections.py      # Discovery/Recommendation/Dialogue views
├── overrides.py        # deterministic user edits
└── repository.py       # domain-owned ports
```

## Canonical profile design

Model explicit concepts rather than arbitrary memory blobs:

- stable interests and avoidances
- emerging interests
- content/style preferences
- creator/language/provider preferences when evidenced
- explicit user overrides
- awareness/insight statements
- confidence, freshness, evidence links, and lifecycle state

Each claim links to evidence and has a deterministic identity. User overrides outrank inferred proposals and cannot be silently overwritten.

## Internal phases

### Phase 1 — Domain model and ownership

- Inventory useful concepts from `soul/profile.py`, memory state, taxonomy, topic lifecycle, overrides, and evidence code.
- Remove duplicate representations and fields without a current product consumer.
- Define frozen typed profile/evidence/claim models and invariants.
- Define repository ports for profile snapshots, evidence, ledger entries, and analyzer checkpoints.
- Add invariant tests before model integration.

### Phase 2 — Observation consumption

- Consume committed observations in bounded batches using per-analyzer checkpoints.
- Build compact typed analyzer inputs containing only relevant evidence.
- Make processing idempotent across retries and restarts.
- Store analyzer proposals before applying them so decisions are auditable.
- Never place raw credentials, full provider payloads, or unbounded history into analyzer context.

### Phase 3 — Analyzer contracts

- Split analyzers by real responsibility, such as preference inference, avoidance inference, topic lifecycle, and higher-level insight.
- Each analyzer has a stable `AgentId`, typed dependencies, input schema, proposal output, capability requirements, and run budget.
- Batch compatible observations rather than running one model call per event.
- Use PydanticAI structured output; remove JSON repair and free-form parser paths.
- Keep analyzers optional: deterministic profile edits and existing profile reads work if models are unavailable.

### Phase 4 — Deterministic commit policy

- Validate proposal evidence, confidence, freshness, contradictions, and allowed field ownership.
- Resolve conflicts using explicit policy, not another LLM call.
- Record accepted, rejected, and superseded proposals in the ledger.
- Apply changes and advance checkpoints atomically.
- Preserve explicit user edits as deterministic overrides with their own audit entries.

### Phase 5 — Bounded projections

- Build `DiscoveryProfile`, `RecommendationProfile`, and `DialogueProfile` from the canonical profile.
- Version each projection and enforce token/character budgets.
- Include only the evidence summaries needed by the consumer.
- Add golden tests for young, mature, sparse, contradictory, and override-heavy profiles.
- Prevent consumers from importing canonical profile internals.

### Phase 6 — Consolidation and legacy removal

- Port proven deterministic rules from category migration, topic lifecycle, posture gates, negative exemplars, and profile builders when they fit the target model.
- Delete unused speculative abstractions and duplicate profile renderers.
- Replace `SoulEngine`, `MemoryManager`, pipeline/consolidator god objects, direct writeback helpers, and scattered JSON state.
- Move dialogue behavior to Assistant and recommendation behavior to Discovery & Recommendation rather than retaining them here.

## Tests and quality gates

- Deterministic tests cover every commit-policy branch and override priority.
- Analyzer tests use PydanticAI deterministic models.
- Replay the same observations twice and assert identical profile/ledger state.
- Golden projection tests enforce content and size budgets.
- Repository tests verify atomic checkpoint/profile updates.
- MyPy exhaustive matching covers proposal and claim variants.

## Evaluation requirements

- Stable analyzer identities expose instructions, output schema, context projection version, model route, and recorded datasets.
- Move retained behavior from `eval/event_simulator.py`, `persona_generator.py`, `persona_judge.py`, `persona_pool.py`, and `speculation_evaluator.py` into understanding-owned scenarios and fixtures using the generic AI evaluation harness.
- Existing useful persona/judge datasets are converted to target schemas; obsolete prompt tests and unused self-optimization paths are deleted.
- Evaluation can compare proposals without writing production profile state.

## Documentation updates during implementation

Replace/update memory and soul module docs, profile schema/privacy docs, architecture diagrams, evaluation docs, and `docs/changelog.md`.

## Completion criteria

- User Understanding is the only canonical profile writer.
- Every inferred claim has evidence and ledger provenance.
- User overrides cannot be overwritten by inference.
- Analyzer runs are short-lived, typed, bounded, replayable, and optional.
- No duplicate memory/profile representation or legacy soul engine remains.
