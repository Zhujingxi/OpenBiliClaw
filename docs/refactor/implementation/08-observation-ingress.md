# Module Plan 08: Observation Ingress

## Outcome

Create `src/openbiliclaw/observations/` as the sole ingress for immutable user-behavior evidence. Producers submit typed observations; this module validates provenance, deduplicates, persists, and publishes committed observation IDs. It never modifies the user profile.

## Target package

```text
observations/
├── models.py          # discriminated observation union
├── provenance.py      # source, trust, account, timestamps
├── producers.py       # ObservationProvider contract
├── validation.py
├── service.py         # record batch and query pending observations
├── repository.py      # domain-owned persistence protocol
└── events.py          # committed notification
```

## Observation model

Use a Pydantic discriminated union with explicit event types, including:

- recommendation shown/opened/liked/disliked/saved/dismissed
- content opened/saved reported by a host
- assistant feedback or preference statement
- deterministic profile edit
- provider-history import

Every observation contains an immutable ID or producer idempotency key, source, occurred-at time, received-at time, optional account ID, optional `ContentRef`, provenance, trust level, and typed event payload. Do not use a generic `event_type + dict` model.

## Internal phases

### Phase 1 — Typed event vocabulary

- Inventory current behavior, feedback, dialogue, history, and profile-edit events.
- Keep only events with a defined consumer or audit purpose.
- Define discriminated payload models and schema versions.
- Define timestamp skew, missing-content, source-trust, and account-identity rules.
- Add serialization and schema evolution tests.

### Phase 2 — Validation and deduplication

- Validate producer identity and allowed event types.
- Normalize only shared metadata; leave provider semantics in provider-owned import code.
- Deduplicate by producer/event idempotency key with a database uniqueness constraint.
- Distinguish duplicate acceptance from validation rejection in typed results.
- Bound batch count and payload size.

### Phase 3 — Persistence and publication

- Persist immutable observations and provenance in one transaction.
- Publish only observation IDs after commit.
- Provide cursor-based reads for Understanding analyzers.
- Keep observation storage unaware of analyzer progress; each consumer owns its processing checkpoints.
- Add replay and restart tests.

### Phase 4 — Built-in producers

- Connect explicit recommendation feedback, Assistant dialogue outcomes, deterministic profile edits, and host content actions.
- Add provider-history import through optional provider capabilities.
- Make producer submission a normal Application Workflow dependency, not an event-hook side effect.
- Ensure unauthenticated host events receive appropriate lower trust and cannot forge account identity.

### Phase 5 — Future producer contract

- Register typed `ObservationProvider` implementations through Core's approved extension category.
- Document how a future browser extension supplies signed/device-authenticated batches.
- Do not implement cross-site trackers, cookie collection, browser sessions, or managed browsers in this refactor.
- Keep browser-specific payloads outside the shared observation contract.

### Phase 6 — Legacy cleanup

- Replace runtime event-ingress and scattered behavior write paths.
- Move validated event filtering/provenance rules from `soul/event_*`, extension handlers, and API routes into the new boundary.
- Delete duplicate event DTOs and direct profile-update calls from producers.

## Tests and quality gates

- Test every event variant and trust rule.
- Test duplicates, out-of-order delivery, clock skew, batch partial failure policy, transaction rollback, and restart.
- Fuzz external JSON validation with a small deterministic corpus; do not add a generic fuzz framework unless needed.
- Verify observations never carry credentials or arbitrary HTML/instructions into model-facing views.
- MyPy must make unhandled observation variants visible through exhaustive matching.

## Documentation updates during implementation

Create an Observation Ingress module doc; update extension, feedback, memory/understanding, API, privacy, architecture, and changelog docs.

## Completion criteria

- All user-understanding inputs enter through one typed, immutable, deduplicated path.
- Producers cannot write the canonical profile.
- Observation replay is deterministic and provenance-preserving.
- Browser observation remains a clean future plugin boundary, not hidden current scope.
