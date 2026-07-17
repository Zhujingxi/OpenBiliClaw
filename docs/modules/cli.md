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
| `openbiliclaw db backup DESTINATION` | Create a private, no-overwrite SQLite snapshot from an inode-pinned main/WAL/SHM/journal source set. |

The old feature commands (`init`, `profile`, `recommend`, source-specific fetch
commands, model editors, updater, and desktop helpers) are not compatibility
aliases. The CLI uses only public Typer/Click APIs.

API entrypoint: `openbiliclaw serve`. Worker entrypoints:
`openbiliclaw worker` and `python -m openbiliclaw.worker`. Compose uses these
same interfaces.
