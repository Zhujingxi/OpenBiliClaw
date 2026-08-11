# Module Plan 02: Infrastructure

## Outcome

Replace the storage and transport monoliths with small concrete adapters under `src/openbiliclaw/infrastructure/`. Domain modules own repository protocols and data models; Infrastructure owns SQLite, filesystem, HTTP, credentials, and event-delivery implementations.

## Target package

```text
infrastructure/
├── sqlite/
│   ├── database.py       # connection/session and transaction primitive
│   ├── schema.py         # target schema and migrations
│   └── repositories/     # one concrete repository per aggregate
├── credentials/
│   ├── vault.py          # CredentialVault implementation
│   └── keyring.py        # OS keyring adapter with protected-file fallback
├── http/
│   ├── clients.py        # shared httpx construction
│   └── policy.py         # timeout, proxy, TLS, user-agent policy
├── events/
│   ├── publisher.py      # in-process typed notifications
│   └── websocket.py      # host delivery adapter
├── files.py
└── telemetry.py
```

## Design rules

- No generic repository base class. Concrete repositories expose only operations their aggregate needs.
- No domain rules in SQL adapters.
- Use Pydantic/domain objects at repository boundaries, not row dictionaries.
- Use parameterized SQL only.
- Transactions are explicit and narrow; nested implicit commits are forbidden.
- Async application code must not block the event loop. Isolate standard-library `sqlite3` work behind a dedicated executor/session boundary rather than adding repository-level `to_thread` calls everywhere.

## Public infrastructure primitives

- `SqliteDatabase`: opens configured database, enforces foreign keys and busy timeout, creates transaction sessions, and closes cleanly.
- `SqliteSession`: typed transaction context passed to concrete repositories when atomic work spans aggregates.
- `CredentialVault`: stores, resolves within a trusted callback, replaces, and deletes secrets by opaque ID. It never returns secrets through transport schemas.
- `HttpClientFactory`: creates scoped `httpx.AsyncClient` instances with explicit lifetime and policy.
- `EventPublisher[EventT]`: typed in-process post-commit notification; not a general workflow bus.
- `TelemetrySink`: structured metrics and traces with mandatory redaction.

## Internal phases

### Phase 1 — Target schema and data policy

- Inventory current tables and map each table to one target aggregate owner.
- Design normalized target tables for access metadata, content references/cache, observations, understanding, recommendation inventory/history, assistant conversations, pending actions, and AI usage attribution.
- Put uniqueness, foreign-key, check, and idempotency invariants in SQLite where appropriate.
- Separate secrets from normal database/configuration records.
- Define a schema version table and atomic migration runner.
- Because compatibility is not required, prefer a clean target schema over preserving malformed legacy columns.
- Before destructive conversion, create and verify a timestamped database/config backup. Abort rather than silently dropping data.

### Phase 2 — Database/session primitive

- Implement connection setup, WAL policy, busy timeout, foreign keys, transaction begin/commit/rollback, and close.
- Use one controlled execution boundary for blocking SQLite calls.
- Prevent repository methods from committing independently when passed a transaction session.
- Add tests for rollback, process restart, uniqueness races, busy handling, and cancellation.

### Phase 3 — Concrete repositories

- Implement repositories as their owner modules define protocols; keep implementations grouped here.
- Split `storage/database.py` methods by aggregate instead of mechanically copying the god object.
- Convert rows to typed objects immediately.
- Keep SQL query helpers local to each repository unless the SQL is genuinely shared.
- Add focused integration tests against temporary real SQLite files.

### Phase 4 — Credential vault

- Use OS keyring support where reliable; use a dedicated permission-checked local secret file as fallback.
- Store only opaque secret IDs in SQLite.
- Expose secret material only inside a trusted resolver callback or provider adapter scope.
- Zero temporary byte buffers where practical and never cache secret strings in domain objects.
- Test permission refusal, missing keyring, replacement, deletion, redaction, and process restart.

### Phase 5 — HTTP, files, and events

- Centralize client lifetime, timeout, TLS, proxy, and retry policy without hiding provider-specific request semantics.
- Permit retries only for safe/idempotent operations unless a provider supplies an idempotency key.
- Implement bounded filesystem paths and atomic writes.
- Deliver typed post-commit events to host transports; product sequencing remains in Application Workflows.
- Add leak tests for clients, files, database handles, and websocket subscribers.

### Phase 6 — Storage cleanup

- Delete `storage/database.py` and obsolete migration helpers after every method has an explicit owner or is proven dead.
- Remove JSON state files superseded by typed repositories.
- Remove duplicated HTTP/client constructors and secret reads from provider/product modules.
- Keep one schema definition and one migration runner.

## Tests and quality gates

- Run repository integration tests against temporary filesystem paths only.
- Verify all SQL constraints directly.
- Add log-capture tests proving secrets and sensitive content are redacted.
- Test concurrent writes and transaction rollback without arbitrary sleeps.
- Repository protocols and implementations must pass MyPy without `Any` row types.

## Documentation updates during implementation

Update `docs/modules/storage.md`, `docs/modules/config.md`, credential/security documentation, deployment backup instructions, architecture diagrams, and `docs/changelog.md`.

## Completion criteria

- No database god object remains.
- Domain modules do not import `sqlite3`, filesystem paths, keyring, or `httpx` construction details.
- No secret exists in `config.toml`, general SQLite tables, logs, API reads, or model-visible data.
- Transactions, connection lifetime, and backup/reset behavior are explicit and tested.
