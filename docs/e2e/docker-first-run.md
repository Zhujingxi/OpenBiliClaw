# Docker first-run E2E

Use a disposable checkout and Compose project. This runbook does not require a live content-platform account; use mocked/test transports unless a separate authorized source test is being performed.

## Automated credential-free regression

Run the maintained Docker product journey before the manual variants:

```bash
bash scripts/test-docker-e2e.sh
```

The script generates per-run secrets, selects free loopback ports, builds the real API and
worker image, and starts application SQLite/Huey, LiteLLM, and LiteLLM PostgreSQL with an empty
model list. After startup, the driver confirms the aliases are unavailable, then creates all
three deployments through LiteLLM's authenticated
[`/model/new` management endpoint](https://docs.litellm.ai/docs/proxy/model_management). Because
`STORE_MODEL_IN_DB=True`, this exercises the same PostgreSQL-backed model-management path used by
LiteLLM Admin rather than loading aliases from test YAML. A test-only OpenAI-compatible service
sits behind LiteLLM and returns schema-derived structured outputs and fixed embeddings. Its
ephemeral API-key value is referenced through a LiteLLM environment variable and is never printed.
The script then restarts only the LiteLLM service, leaves PostgreSQL running, waits at most two
minutes for proxy health, and requires all three aliases to be healthy again before continuing.
This makes database persistence and reload part of the first-run gate. The driver completes Zhihu
browser work only through generic source-task
claim/complete, then verifies onboarding, evidence profile, initial feed, semantic negative
feedback, a later profile projection, changed ordering of distinct later candidates, chat, and
favorites/watch-later add/list/remove. No live credential or platform mutation is used.

The exit trap always runs `docker compose down --volumes --remove-orphans` for the unique
Compose project. A failure prints bounded service logs first. Pass only when the command prints
`Docker product E2E passed: DB-backed alias setup` and no container with its generated project
prefix remains.

## 1. Prepare and start

```bash
git clone https://github.com/whiteguo233/OpenBiliClaw.git openbiliclaw-e2e
cd openbiliclaw-e2e
COMPOSE_PROJECT_NAME=openbiliclaw-e2e MODE=docker bash scripts/install.sh
docker compose ps
```

Pass when `migrate` exited with code 0 and `api`, `worker`, `litellm`, and `litellm-postgres` are healthy/running. Fail if the installer exits nonzero or only API is healthy.

## 2. Configure aliases

Open `http://127.0.0.1:4000/ui`. Configure test deployments and create these exact model groups:

```text
obc-interactive
obc-analysis
obc-embedding
```

Use a local mock/test model where possible. If a live provider is used, record only provider/model names and outcome, never a credential. Pass when `GET /api/v1/system/ai-health` shows all required aliases available.

## 3. Verify public and protected health

```bash
curl -fsS http://127.0.0.1:8420/api/v1/system/readiness
```

Read the installer bearer inside a process so it does not appear in shell history or command arguments:

```bash
python3 - <<'PY'
import json
import urllib.request
from pathlib import Path

env = dict(
    line.split("=", 1)
    for line in Path(".env").read_text().splitlines()
    if line and not line.startswith("#") and "=" in line
)
request = urllib.request.Request(
    "http://127.0.0.1:8420/api/v1/settings",
    headers={"Authorization": f"Bearer {env['OPENBILICLAW_ACCESS_TOKEN']}"},
)
with urllib.request.urlopen(request, timeout=10) as response:
    assert response.status == 200
    assert isinstance(json.load(response), dict)
PY
```

Also confirm the same protected request without authorization returns 401 and a typed error envelope.

## 4. Run first setup

Open `http://127.0.0.1:8420/setup/`.

1. Before any onboarding call, confirm anonymous access returns `401`, then log in with the Web password from the installer's one-time `first_run_access` event. Confirm a second installer run does not emit it again and `.env` contains neither plaintext value.
2. Confirm readiness, the three alias checks, and the visible `http://127.0.0.1:4000/ui` Admin link.
3. Select a credential-bearing test source and fill the fields rendered from its manifest. Confirm an empty-schema browser source has no credential input.
4. Before onboarding, wait through at least one worker periodic tick and confirm `GET /api/v1/jobs` contains no periodic `source_sync`, `profile_projection`, or `feed_replenishment` run. A periodic `cleanup` run is permitted.
5. Start onboarding and verify account configuration is sent before onboarding. The response must not echo credentials.
6. Observe the authenticated SSE stream from `POST /api/v1/onboarding/start` through `GET /api/v1/onboarding/{run_id}/events`.
7. Confirm the explicitly scheduled source sync, profile projection, and feed replenishment child stages still run before onboarding is marked complete.

Pass only when the terminal event is successful and `onboarding_complete` is persisted. After completion, wait for the next due maintenance bucket and confirm periodic jobs are admitted with scheduled priority. Repeat onboarding once with a controlled child failure and once with cancellation; UI and SSE must preserve those terminal states.

Run two preparation installers concurrently in the disposable checkout and hold the first at the
Compose step. Confirm the second does not stage access until the first releases the lifecycle
lock, and that only the successful owner's verifier/disclosure is committed. Set an explicit
credential-free custom Admin URL, rerun without the option, and confirm setup preserves that URL.
Attempt to PATCH `web_password_enabled=false` through the logged-in Web contract and require a
typed `422` with the existing login still usable.

## 5. Complete the product journey

In `http://127.0.0.1:8420/web/`:

1. inspect the evidence profile and apply one explicit edit;
2. replenish and inspect the feed;
3. submit feedback, replenish again, and record the later ranking change;
4. stream a chat reply and verify history;
5. add one item to favorites and watch later, list both, then remove it locally.

No platform account may change.

## 6. Recovery and teardown

Restart worker while a disposable pending job exists, then verify the same `job_runs` record resumes without duplicate business effects.

```bash
docker compose restart worker
docker compose ps
docker compose logs --no-color api worker
docker compose down
```

Use `docker compose down -v` only for this disposable project after confirming the project name. Verify no `openbiliclaw-e2e` container remains.
