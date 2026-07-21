# Source-install E2E

Use a disposable checkout and a test LiteLLM Proxy. Do not use a production database or live credentials.

## 1. Prepare

```bash
git clone https://github.com/whiteguo233/OpenBiliClaw.git openbiliclaw-source-e2e
cd openbiliclaw-source-e2e
uv sync --frozen
```

Provide `OPENBILICLAW_LITELLM_BASE_URL` and `OPENBILICLAW_LITELLM_API_KEY` through the current process or secret manager. Do not paste values into the command line or report.
Optionally provide a separately verified `OPENBILICLAW_LITELLM_ADMIN_URL` (or
`--litellm-admin-url`) when the browser should show an Admin link. Confirm the link is absent when
this value is omitted; the installer must not derive it from the model API base URL.

## 2. Preparation-only path

```bash
MODE=local SKIP_START=1 bash scripts/install.sh
uv run openbiliclaw db migrate
uv run openbiliclaw doctor
```

Pass when the fresh database is at Alembic head, `data/vnext/huey.db` is separate, and no API/worker process was started by the preparation-only path.
Capture the first-run structured access event privately. Confirm `.env` contains the password
hash and extension digest records but neither disclosed plaintext value; rerun preparation and
confirm the event is not emitted again.
Force migration failure in a disposable checkout and confirm neither verifier record nor event
exists. Simulate a lost successful event, rerun with `ROTATE_ACCESS=1`, and confirm one new pair
replaces both old verifier records.

## 3. Managed runtime

```bash
MODE=local bash scripts/install.sh
curl -fsS http://127.0.0.1:8420/api/v1/system/readiness
uv run openbiliclaw doctor
```

Verify both managed API and worker identities exist, their logs are separate, both use the same environment and application database, and protected readiness succeeds without printing the bearer.

## 4. Operational commands

In a separate disposable environment, verify foreground entry points and graceful interruption:

```bash
uv run openbiliclaw serve --host 127.0.0.1 --port 8420
uv run openbiliclaw worker
uv run openbiliclaw eval
```

Do not source `.env` manually for this step. Confirm each command reads the installer-written
runtime paths through a no-follow descriptor, while an explicitly exported `OPENBILICLAW_*`
value still takes precedence. A symlink, non-regular file, wrong owner, or POSIX mode other than
`0600` must fail closed without reading the target.

On native Windows, additionally confirm the installer applies a protected DACL owned by the
current user SID with no inherited or additional access rules before writing `.env`. Change the
owner or add another allow rule and require the next installer/CLI read to fail closed. A missing
PowerShell ACL facility is a failed installation, not a warning.

Then verify backup publishes a new destination without overwriting an existing one:

```bash
uv run openbiliclaw db backup /absolute/disposable/path/openbiliclaw-backup.db
```

Run SQLite integrity on the backup and compare expected schema revision. On unsupported platforms, pass only if backup fails before reserving the destination with a clear operational error.

## 5. Restart recovery

Create a disposable pending job, stop the worker after transport delivery but before application completion, and restart through the installer. Confirm the same `job_runs` record resumes and business effects remain idempotent. Repeat the installer to verify secrets are reused and migration is idempotent.

## 6. Product smoke and cleanup

Follow the [Web runbook](web.md) against the source backend and an external test LiteLLM. At completion, rerun the installer in preparation-only mode to perform ownership-checked shutdown, or stop the exact recorded managed pair through the supported lifecycle. Verify no API/worker process remains and retain no `.env`, database, queue, log, or backup artifact outside the disposable checkout.
