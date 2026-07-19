# E2E Docker Compose Stack

Durable, in-repo Docker Compose definition for the OpenBiliClaw E2E stack.
It replaces the legacy `/tmp/obc-e2e-main` checkout (which was wiped on
reboot) with the exact same running topology.

## What it runs

- `obc-e2e-backend` — the API server, built from the repo checkout,
  published on host port **18421** (container port 8420). The E2E harness
  targets `http://127.0.0.1:18421`.
- `obc-e2e-ollama` — the bundled Ollama sidecar (bge-m3 baked into the
  image), used by the backend for embeddings.
- `OPENBILICLAW_SEED_OLLAMA_DEFAULTS=1` and
  `OPENBILICLAW_OLLAMA_BASE_URL=http://ollama:11434/v1` are set on the
  backend, matching the legacy /tmp stack.

The main user-facing stack on port 8420 is a separate Compose project and is
never touched by this one.

## First-time setup (fresh machine)

```bash
cd <repo-root>
docker compose \
  -f deploy/e2e/docker-compose.yml \
  -f deploy/e2e/docker-compose.e2e-override.yml \
  up -d --build
```

The override file pins four pre-existing Docker volumes (originally created
under Compose project name `obc-e2e-main` by the legacy /tmp stack) so the
E2E backend's first-run state (setup wizard not yet completed,
`profile_ready: false`) and the seeded bge-m3 model survive relocation and
rebuilds. On a machine where those volumes do not exist yet, create them
first:

```bash
for v in config data logs ollama; do
  docker volume create "obc-e2e-main_openbiliclaw_${v}"
done
```

## Rebuild after origin/main moves

```bash
cd <repo-root>
git pull   # or check out the commit you want to test
docker compose \
  -f deploy/e2e/docker-compose.yml \
  -f deploy/e2e/docker-compose.e2e-override.yml \
  up -d --build
```

Volumes are kept, so first-run state is preserved across rebuilds.

## Reset to first-run state

The four pinned volumes are declared `external` in the override, so Compose
never deletes them (`down -v` leaves them in place). A real reset must stop
the stack, remove the volumes by name, recreate them empty — Compose will
not auto-create external volumes, so skipping this step makes the next `up`
fail — and then rebuild:

```bash
docker compose \
  -f deploy/e2e/docker-compose.yml \
  -f deploy/e2e/docker-compose.e2e-override.yml \
  down
docker volume rm \
  obc-e2e-main_openbiliclaw_config \
  obc-e2e-main_openbiliclaw_data \
  obc-e2e-main_openbiliclaw_logs \
  obc-e2e-main_openbiliclaw_ollama
for v in config data logs ollama; do
  docker volume create "obc-e2e-main_openbiliclaw_${v}"
done
docker compose \
  -f deploy/e2e/docker-compose.yml \
  -f deploy/e2e/docker-compose.e2e-override.yml \
  up -d --build
```

## Verify

```bash
curl http://127.0.0.1:18421/api/ping     # -> ok
curl http://127.0.0.1:18421/api/health   # -> includes "profile_ready": false on first run
```
