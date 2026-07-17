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

This is the supported easy-install path for source-built deployments. The quick-start block in `docker-compose.prebuilt.yml` provides the equivalent locked, atomic first-run transaction for the released image and bundled LiteLLM policy.
The prebuilt two-file flow downloads that policy to `litellm/config.yaml` before running the transaction.

## Secrets

The installer creates and reuses these private values:

- `LITELLM_POSTGRES_PASSWORD`;
- `LITELLM_MASTER_KEY`;
- `OPENBILICLAW_SECRET_KEY`;
- `OPENBILICLAW_ACCESS_TOKEN`;
- `OPENBILICLAW_SESSION_SECRET`.
- `OPENBILICLAW_WEB_PASSWORD_HASH` (scrypt hash only);
- `OPENBILICLAW_EXTENSION_ACCESS_KEYS` (digest records only).

Only a successfully migrated and healthy runtime commits the Web password hash/extension digest and emits their plaintext pair once. Failed Compose runs disclose neither value. The installer lifecycle lock covers staging, Compose start, commit, disclosure, and `ROTATE_ACCESS=1`, so concurrent runs cannot cross-persist credential pairs. Use rotation when a successful event was lost. The credential-free `OPENBILICLAW_LITELLM_ADMIN_URL` defaults to `http://127.0.0.1:${LITELLM_PORT:-4000}/ui`; an explicit `--litellm-admin-url` replaces and persists it, while an unconfigured rerun preserves the current custom URL. Provider credentials go only into LiteLLM Admin.

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

Set `HOST` and `PORT` on the installer to change the public API binding. Those values drive the Compose mapping, API listen port, healthcheck and installer probe together; LiteLLM Admin remains loopback-only unless separately configured.

The installer-provisioned Web password is required before first-run onboarding even on a fresh
database. Product settings cannot disable that login path; use successful access rotation for
credential recovery.

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

Log in with the first-run Web password. Setup renders backend credential inputs directly from each manifest's `credential_schema`, configures selected credential-bearing accounts before onboarding, and shows no unusable credential form for extension-only sources. Browser-assisted sources require the extension and its one-time access key. Follow the [Docker first-run](e2e/docker-first-run.md), [Web](e2e/web.md), and [extension](e2e/extension.md) runbooks.

## Operations

```bash
docker compose up -d --build
docker compose logs -f api worker
docker compose restart api worker
docker compose down
```

`docker compose down` preserves volumes. `down -v` destroys the vNext database, queue, and LiteLLM database and must be used only when destructive reset is explicitly intended. Old host data files are not imported or deleted.
