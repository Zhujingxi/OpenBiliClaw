# Module Plan 11: Application Workflows

## Outcome

Create `src/openbiliclaw/application/` as the explicit coordination layer for cross-module product use cases. Workflows call typed services and repositories directly; there is no command bus, workflow DSL, event choreography, or god orchestrator.

## Target package

```text
application/
├── context.py                 # typed application dependencies/facade
├── connect_source.py
├── record_observation.py
├── record_feedback.py
├── refresh_recommendations.py
├── get_recommendations.py
├── search_content.py
├── get_content_details.py
├── edit_profile.py
├── handle_dialogue_action.py
└── errors.py
```

Use one file per cohesive use case only when that file carries real logic. Trivial read operations may share a module.

## Contract style

Each workflow exposes a typed command/query model and typed result. Dependencies are explicit constructor parameters in a frozen dataclass or function parameters. Workflows may depend on domain services and repository/unit transaction ports but never discover dependencies globally.

Mutations define:

- validation boundary
- authorization requirement
- transaction boundary
- idempotency behavior
- post-commit notifications
- audit fields

## Internal phases

### Phase 1 — Workflow inventory

- Inventory HTTP routes, CLI commands, Assistant operations, scheduled callbacks, and extension messages.
- Map each product operation to exactly one workflow owner.
- Delete operations that expose internals or have no target product use.
- Separate queries from mutations without adding CQRS infrastructure.
- Produce a route/command/tool-to-workflow matrix used by later host plans.

### Phase 2 — Read workflows

- Implement source status/listing, recommendation reads, profile projections, search, content detail, job health, and conversation reads.
- Return application result models rather than transport response classes.
- Enforce pagination and result-size limits.
- Keep reads model-free unless the workflow explicitly invokes a domain agent capability.

### Phase 3 — Mutation workflows

- Implement connect/disconnect source, feedback, profile edit, observation import, recommendation refresh request, and content actions.
- Make idempotency keys mandatory for retried external or host mutations.
- Commit primary state before publishing typed notifications.
- Run provider mutation actions only after permission and confirmation checks.
- Ensure partial external failure produces an explicit recoverable state rather than inconsistent local writes.

### Phase 4 — Feedback and observation sequencing

- `RecordFeedback` writes recommendation feedback and observation state in one defined transactional sequence.
- `EditProfile` writes the deterministic override and its observation/audit entry.
- `RefreshRecommendations` requests bounded work through the recommendation service/Core supervisor rather than starting tasks itself.
- `ConnectSource` delegates credential storage and verification to Access, then refreshes provider availability.
- Add restart/idempotency tests for each sequence.

### Phase 5 — External action confirmation

- Define `PendingAction` with expiry, user/account scope, action schema, safe preview, and idempotency key.
- Assistant and hosts may propose an action but cannot execute it without explicit confirmation where required.
- Revalidate access and content state at execution time.
- Do not store raw credentials or model-generated executable payloads in pending actions.

### Phase 6 — Consumer cutover

- Replace direct repository/service calls in API, CLI, Assistant, runtime jobs, and extension endpoints with workflows.
- Delete duplicate transaction and sequencing logic from hosts.
- Remove old integration operations that act as service locators or broad facades.
- Keep domain-internal operations inside their modules instead of wrapping every function as a workflow.

## Tests and quality gates

- Unit tests use typed fakes for domain services and repositories.
- Transactional integration tests use real temporary SQLite infrastructure.
- Contract tests verify each host/tool maps to one workflow.
- Idempotency, rollback, authorization, confirmation expiry, and cancellation are tested.
- MyPy prevents transport schemas and infrastructure implementations from leaking into workflow contracts.

## Documentation updates during implementation

Update API, CLI, integrations, runtime, and affected product module docs; update architecture/data-flow diagrams and `docs/changelog.md`.

## Completion criteria

- Every cross-module user-visible operation has one explicit workflow.
- Hosts and Assistant contain no product sequencing or direct persistence.
- No generic command bus, event choreography, or service locator exists.
- Transaction, authorization, idempotency, and confirmation boundaries are visible and tested.
