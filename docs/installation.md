# Installation

OpenBiliClaw supports Docker Compose and source/`uv` installations. Docker is recommended. Both modes run the same API, worker, migrations, Web assets, and extension contract.

## Requirements

- Docker Compose v2 for Docker mode; or Python 3.11+ and `uv` for source mode;
- Git for the supported installer path;
- a LiteLLM deployment: included in Docker mode, external in source mode;
- a modern Chromium browser or Firefox for the extension.

## Docker

```bash
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw
MODE=docker bash scripts/install.sh
```

The installer creates a private `.env`, applies Alembic through the one-shot `migrate` service, and requires both API and worker health before success. One lifecycle lock serializes secret staging, Compose start, verifier commit, one-time disclosure, and explicit rotation, so concurrent installers cannot start with one pair and persist or disclose another. Browser credentials remain staged in memory until those checks pass; only then are their scrypt hash/key digest atomically committed and one `first_run_access` event emitted. Failed installs neither commit those records nor disclose plaintext. Save a successful event immediately. If the process crashes after the final commit but before the event is captured, rerun with `ROTATE_ACCESS=1` to replace the unusable pair and receive a new event. The installer does not configure provider credentials or connect sources.

Next:

1. open `http://127.0.0.1:4000/ui`;
2. configure LiteLLM deployments and the exact aliases `obc-interactive`, `obc-analysis`, and `obc-embedding`;
3. open `http://127.0.0.1:8420/setup/` and log in with the one-time Web password;
4. select a source, fill only the credential fields declared by its manifest, and start bootstrap;
5. use `http://127.0.0.1:8420/web/` or the extension.

See [Docker deployment](docker-deployment.md) and the [Docker first-run runbook](e2e/docker-first-run.md).

## Source / uv

Source mode requires an external LiteLLM Proxy. Let the installer prompt securely, or set the connection in the current process through an appropriate secret store:

```text
OPENBILICLAW_LITELLM_BASE_URL
OPENBILICLAW_LITELLM_API_KEY
```

Then run:

```bash
MODE=local bash scripts/install.sh
```

The installer synchronizes dependencies, writes private runtime environment, migrates `data/vnext/openbiliclaw.db`, starts `openbiliclaw serve` and `openbiliclaw worker`, runs `doctor`, verifies protected readiness, and only then commits/emits first-run access. Operational CLI commands securely load this installer-written `.env` from the checkout without replacing explicitly exported process values. See the [source-install runbook](e2e/source-install.md).

## Browser access

Web password hash, session signing secret, and extension device-key digests are infrastructure credentials. The installer generates all three access paths. Plaintext Web/extension bootstrap values appear only in the first-run structured event and must never be copied into `.env`, commands, docs, logs, screenshots, or generated clients.

The supported first-run setup requires this password login before onboarding, including when
the API is bound to the network. Web password enablement is read-only through product settings;
recover a lost password with `ROTATE_ACCESS=1`, never by disabling authentication.

The complete extension device key is delivered once for entry in the target extension. Runtime configuration stores only `key-id:sha256-digest`. Web and extension sessions are revocable and separate from the installer bearer.

`HOST` and `PORT` are honored by Docker installation and persisted as the Compose public bind host/port. The container command, port mapping, healthcheck, installer probe, and reported URL use the selected port.
`LITELLM_PORT` similarly drives Docker's default Admin link. An explicit credential-free
`OPENBILICLAW_LITELLM_ADMIN_URL`/`--litellm-admin-url` replaces the Docker link and is persisted;
a rerun with no explicit value preserves that custom URL. Source installs do not infer a public
browser URL from the model API endpoint. Otherwise the source-install link is absent.

## Data and upgrades

The vNext application database defaults to `data/vnext/openbiliclaw.db`; Huey uses `data/vnext/huey.db`. Existing v0.3 data paths remain untouched and are not imported.

Before an upgrade, create a backup:

```bash
openbiliclaw db backup /absolute/path/openbiliclaw-vnext-backup.db
```

Then rerun the installer. It reuses non-empty secrets and reapplies idempotent migrations.

## Troubleshooting

```bash
openbiliclaw doctor
curl -fsS http://127.0.0.1:8420/api/v1/system/readiness
```

For Docker also run `docker compose ps` and inspect `api` and `worker` logs. Never print `.env` while troubleshooting.
