# OpenBiliClaw vNext installer contract

This is the active contract for humans and coding agents. It replaces all v0.x
provider-editor and feature-command setup flows.

## Supported outcomes

The installer prepares one of two runtimes:

- **Docker, recommended:** Compose manages one-shot `migrate`, `api`, `worker`,
  `litellm`, and `litellm-postgres`.
- **Source / uv:** the installer manages both `openbiliclaw serve` and
  `openbiliclaw worker` and connects them to a user-supplied LiteLLM proxy.

The existing static Web and extension assets remain mounted, but their vNext API
wiring is pending Task 22. Do not describe the legacy UI as a completed setup path.

## One-line entry points

macOS, Linux, and WSL2:

```bash
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.sh | bash
```

Native Windows PowerShell:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex
```

`MODE=auto` prefers Docker Compose v2 and otherwise selects a source install.
Set `MODE=docker` or `MODE=local` to make the choice explicit. Both scripts reuse an
existing checkout and preserve local changes; an unknown non-empty target directory
is rejected.

## Secret handling

The authoritative runtime file is `<checkout>/.env`. It is ignored by Git, written
through a same-directory temporary file and atomic replace, and uses mode `0600` on
POSIX. `.env` and its lock must be regular files; symlinks are rejected. Existing
non-empty secrets and the external LiteLLM connection are reused on every rerun.
Installer-owned `OPENBILICLAW_PROJECT_ROOT`, installer instance ID, application DB,
and Huey paths are always rebound to the current canonical checkout and private
instance metadata, so copying `.env` cannot keep writing to the original checkout.

Docker generates:

- `LITELLM_POSTGRES_PASSWORD`
- `LITELLM_MASTER_KEY`
- `OPENBILICLAW_SECRET_KEY`
- `OPENBILICLAW_ACCESS_TOKEN`

Source installs additionally require user values for:

- `OPENBILICLAW_LITELLM_BASE_URL`
- `OPENBILICLAW_LITELLM_API_KEY`

The shell and PowerShell installers collect a missing LiteLLM key with terminal echo
disabled. Status events contain only step names, paths, process IDs, and a health
URL; they never contain secret values or the LiteLLM URL.

## Source-install sequence

The order is intentionally fixed and failures propagate. A dedicated bounded
cross-process lifecycle lock serializes the complete sequence; it is separate from
the short `.env` writer lock, so concurrent prepare/start invocations cannot overlap
migrations or publish competing process pairs. Its stable private metadata file is
itself the locked inode: the held parent-directory FD, embedded root/device/inode,
and pathname identity are checked before and after the lifecycle. Replaced/copied
lock inodes and symlinked project ancestors fail closed instead of creating a second
lock domain:

1. Install dependencies with `uv sync --frozen`, or a Python editable fallback.
2. Persist stable access/encryption secrets and the supplied LiteLLM connection.
3. Set the application database to `data/vnext/openbiliclaw.db`.
4. Set Huey transport to the separate `data/vnext/huey.db`.
5. Stop only a previous API/worker whose PID, OS start time, executable, and
   command fingerprint still match this installer's private state.
6. Run `openbiliclaw db migrate` before launching either new process.
7. Start `openbiliclaw serve` and `openbiliclaw worker` with the exact same env.
8. Poll both child processes and require the worker queue, then run
   `openbiliclaw doctor`.
9. Check public readiness and a bearer-protected settings request, followed by
   another API and worker liveness check.

The installer persists a private UUID in `data/vnext/installer-instance.json` and
binds process state to that UUID, the canonical checkout root, and a monotonic
generation. Verified identities are stored privately in
`data/vnext/runtime-processes.json`; bare or stale PIDs are never signalled. Copied,
moved, malformed, or ownership-mismatched state is refused instead of managed.
Managed shutdown sends TERM, waits for a bounded interval, and escalates only while
the same identity is still present. A present state path must be a regular file;
directories, FIFOs, symlinks, and other objects fail closed. Stop and failure cleanup
never pathname-unlink process state. The ownership-bound dead record remains until
the next ownership-checked process-state publication, so stale cleanup cannot delete
a newer generation.
Logs are separate at `logs/api.log` and `logs/worker.log`. A failed migration starts
nothing. A partial launch, state-write failure, dead worker, or failed protected
check terminates and reaps every newly started child and returns non-zero.

For CI or image preparation, set `SKIP_START=1`; this still installs, persists the
environment, verifiably stops a previously managed local pair, and migrates, but
does not daemonize either process or run the live worker/LiteLLM checks in `doctor`.
In Docker mode it runs the isolated
`docker compose run --rm migrate` service before returning `docker_runtime_prepared`.

## Docker sequence

Docker mode atomically fills missing infrastructure secrets and runs Compose. The
one-shot `migrate` service applies Alembic first; `api` and `worker` both require its
successful completion and only perform a read-only schema-head startup check. A
migration failure therefore blocks both long-running processes. The installer then
requires Compose to report `migrate` exited with code zero and both API and worker as
`running/healthy`, then verifies public and bearer-protected API access and rechecks
the same Compose status before success. A restarting,
exited, or unhealthy worker fails the install even if API readiness succeeds. The
worker healthcheck validates the PID 1 worker command, schema head, queue integrity,
and real schema/data mutation inside a `BEGIN IMMEDIATE` transaction that is rolled
back without leaving a probe artifact. The queue pathname must still match the held
descriptor before and after SQLite access. The three application services mount
`openbiliclaw_data:/app/runtime/data`; API and worker use exactly:

```text
OPENBILICLAW_DATABASE_URL=sqlite:////app/runtime/data/vnext/openbiliclaw.db
OPENBILICLAW_HUEY_PATH=/app/runtime/data/vnext/huey.db
```

Provider credentials are configured only in LiteLLM Admin. Create model groups
`obc-interactive`, `obc-analysis`, and `obc-embedding`; do not add provider editors
to OpenBiliClaw.

## Machine-readable result

Every major result is one JSON line prefixed with `BOOTSTRAP_STATUS:`. Success is
`message=local_runtime_ready` or `message=docker_runtime_ready`; preparation-only is
`*_runtime_prepared`. Any dependency, migration, process, Compose, or readiness error
returns non-zero with `message=bootstrap_failed` and only the exception type.
