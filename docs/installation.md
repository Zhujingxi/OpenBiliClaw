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

The installer creates a private `.env`, applies Alembic through the one-shot `migrate` service, and requires both API and worker health before success. It does not configure provider credentials or connect sources.

Next:

1. open `http://127.0.0.1:4000/ui`;
2. configure LiteLLM deployments and the exact aliases `obc-interactive`, `obc-analysis`, and `obc-embedding`;
3. open `http://127.0.0.1:8420/setup/`;
4. connect sources and start bootstrap;
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

The installer synchronizes dependencies, writes private runtime environment, migrates `data/vnext/openbiliclaw.db`, starts `openbiliclaw serve` and `openbiliclaw worker`, runs `doctor`, and verifies protected readiness. See the [source-install runbook](e2e/source-install.md).

## Browser access

Web password hash, session signing secret, and extension device-key digests are infrastructure credentials. The installer creates the session signing secret. Provision optional password and extension access directly in the private `.env` or deployment secret store; never place plaintext credentials in commands, docs, logs, screenshots, or generated clients.

The complete extension device key is delivered once to the target extension. Runtime configuration stores only `key-id:sha256-digest`. Web and extension sessions are revocable and separate from the installer bearer.

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
