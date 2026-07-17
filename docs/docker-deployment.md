# Docker deployment

Docker Compose is the recommended deployment. It runs:

- `migrate`: one-shot Alembic upgrade;
- `api`: FastAPI and static Web;
- `worker`: Huey jobs;
- `litellm`: model proxy and Admin UI;
- `litellm-postgres`: LiteLLM configuration database.

OpenBiliClaw product data remains in SQLite. PostgreSQL belongs only to LiteLLM.

## Start from a checkout

```bash
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw
MODE=docker bash scripts/install.sh
```

This is the supported easy-install path for both source-built and prebuilt deployments. `docker-compose.prebuilt.yml` is intended for release automation or operators who already manage the required private `.env` and `litellm/config.yaml`; it is not a second interactive installer.

## Secrets

The installer creates and reuses these private values:

- `LITELLM_POSTGRES_PASSWORD`;
- `LITELLM_MASTER_KEY`;
- `OPENBILICLAW_SECRET_KEY`;
- `OPENBILICLAW_ACCESS_TOKEN`;
- `OPENBILICLAW_SESSION_SECRET`.

Optional browser access uses `OPENBILICLAW_WEB_PASSWORD_HASH`, digest-only `OPENBILICLAW_EXTENSION_ACCESS_KEYS`, and credential-free `OPENBILICLAW_LITELLM_ADMIN_URL`. Provider credentials go only into LiteLLM Admin.

The `.env` file must remain a private regular file and must not be committed, printed, attached to issues, or included in screenshots. Do not reuse secrets across purposes.

Validate the Compose render without printing interpolated values:

```bash
docker compose config --quiet
```

This Compose render check validates structure only; runtime health still requires migration,
API, worker, LiteLLM, and PostgreSQL checks below.

## Data layout

API and worker share the same named data volume and exact paths:

```text
OPENBILICLAW_DATABASE_URL=sqlite:////app/runtime/data/vnext/openbiliclaw.db
OPENBILICLAW_HUEY_PATH=/app/runtime/data/vnext/huey.db
```

Huey result data is transport state. `job_runs` in the application database is the product status authority.

## Migration and health

`migrate` is the only service allowed to write Alembic schema. API and worker depend on its successful completion and then perform a read-only schema-head gate.

```bash
docker compose ps
curl -fsS http://127.0.0.1:8420/api/v1/system/readiness
curl -fsS http://127.0.0.1:4000/health/readiness
docker compose logs api worker
```

Installation succeeds only when migration exits zero and API and worker are healthy. API readiness alone is not proof that jobs can run.

## LiteLLM

Open `http://127.0.0.1:4000/ui` and configure deployments for:

- `obc-interactive`;
- `obc-analysis`;
- `obc-embedding`.

LiteLLM owns provider credentials, routing, fallback, retry, cooldown, rate limits, budgets, and cache. The bundled single-proxy deployment selects LiteLLM's process-local cache explicitly, so it does not require Redis; operators who deploy a multi-replica external proxy may choose shared cache infrastructure there. OpenBiliClaw does not expose a provider editor. Set `OPENBILICLAW_LITELLM_ADMIN_URL` only to a safe credential-free URL that clients may open.

## First run

After the three aliases are healthy, open:

- setup: `http://127.0.0.1:8420/setup/`;
- desktop Web: `http://127.0.0.1:8420/web/`;
- mobile Web: `http://127.0.0.1:8420/m/`.

Connect sources through setup. Browser-assisted sources require the extension and its finite-bearer provisioning. Follow the [Docker first-run](e2e/docker-first-run.md), [Web](e2e/web.md), and [extension](e2e/extension.md) runbooks.

## Operations

```bash
docker compose up -d --build
docker compose logs -f api worker
docker compose restart api worker
docker compose down
```

`docker compose down` preserves volumes. `down -v` destroys the vNext database, queue, and LiteLLM database and must be used only when destructive reset is explicitly intended. Old host data files are not imported or deleted.
