# vNext CLI

`openbiliclaw` is an operations interface only. Product workflows belong to the
web app, browser extension, `/api/v1`, or the four durable jobs.

## Public commands

| Command | Purpose |
|---|---|
| `openbiliclaw serve [--host …] [--port …]` | Run the vNext FastAPI app and mount the existing static web. |
| `openbiliclaw worker [--workers 1..4]` | Run the bounded Huey consumer. |
| `openbiliclaw doctor` | Report database/migration, queue integrity and write access, access-token, and LiteLLM configuration state without printing secret values. |
| `openbiliclaw eval [--dataset NAME]` | Validate versioned offline Pydantic Evals datasets; no provider call. |
| `openbiliclaw db migrate` | Upgrade the configured fresh vNext database with Alembic. |
| `openbiliclaw db backup DESTINATION` | Pin the main database and existing sidecars with no-follow FDs, prove the SQLite read connection opened only those held identities before and after `sqlite3_backup`, then publish through an atomic no-replace primitive (locked held-FD `fclonefileat` on macOS; unlinked `O_TMPFILE` + `linkat(AT_EMPTY_PATH)` on Linux). Source snapshots create no named hard-links or cleanup directories; the final bytes, SQLite integrity, and pathname/FD are revalidated after directory sync. Windows or a platform without the primitive fails before destination reservation. |

The old feature commands (`init`, `profile`, `recommend`, source-specific fetch
commands, model editors, updater, and desktop helpers) are not compatibility
aliases. The CLI uses only public Typer/Click APIs.

API entrypoint: `openbiliclaw serve`. Worker entrypoints:
`openbiliclaw worker` and `python -m openbiliclaw.worker`. Compose uses these
same interfaces.

For source installs, the root callback reads the installer-owned `<checkout>/.env` before every
operational command. Only `OPENBILICLAW_*` entries are imported and existing process environment
values are never overwritten. This makes `serve`, `worker`, `doctor`, `eval`, and `db` commands
consume the same persisted runtime paths/secrets without requiring a manual `source .env`.
The read rejects symlinks/non-regular files, verifies pathname/descriptor identity and current-user
ownership, and requires POSIX mode `0600` or a verified protected Windows DACL granting only the
current user SID full control.

Web-password enablement is not an operational CLI/product-settings toggle. If the one-time
password disclosure is lost, rerun the supported installer with `ROTATE_ACCESS=1` (or invoke
`scripts/runtime_bootstrap.py --rotate-access`); verifier replacement and redisclosure occur only
after the locked runtime verification succeeds.
