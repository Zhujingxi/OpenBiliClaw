# Docker Backend Deployment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add one-command Docker backend deployment for local and server use without breaking the existing CLI workflow.

**Architecture:** Introduce a dedicated container entrypoint via `openbiliclaw serve-api`, make API host/port configurable in the CLI, and package the backend with a single-service `docker-compose.yml` that mounts config, data, and logs from the repository root.

**Tech Stack:** Python 3.11, Typer, FastAPI, Uvicorn, Docker, Docker Compose, Pytest

---

### Task 1: Add CLI coverage for container-friendly API startup

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Write the failing tests**

Add tests that assert:

- `openbiliclaw start` calls `_run_api_server(host="127.0.0.1", port=8420)`
- `openbiliclaw start --host 0.0.0.0 --port 9000` forwards both values
- `openbiliclaw serve-api` calls `_run_api_server(host="0.0.0.0", port=8420)`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -k "start or serve_api" -v`

Expected: FAIL because `serve-api` does not exist yet and `start` does not accept host/port options.

**Step 3: Write minimal implementation**

In `src/openbiliclaw/cli.py`:

- Keep `_run_api_server(host, port)` as the shared runner
- Add `host` and `port` options to `start`
- Add a new `serve-api` command with container defaults

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -k "start or serve_api" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_cli.py src/openbiliclaw/cli.py
git commit -m "feat: add container-friendly api startup commands"
```

### Task 2: Add Docker packaging files

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `docker-compose.yml`

**Step 1: Write the failing test**

No Python unit test is appropriate here. Instead, define the concrete acceptance checks:

- `Dockerfile` builds from a Python slim base image
- container command runs `openbiliclaw serve-api --host 0.0.0.0 --port 8420`
- `docker-compose.yml` exposes `8420:8420`
- compose mounts `config.toml`, `data/`, and `logs/`

**Step 2: Run check to verify current state fails**

Run: `ls Dockerfile .dockerignore docker-compose.yml`

Expected: missing-file errors

**Step 3: Write minimal implementation**

Create:

- `Dockerfile` with install, copy, and default command
- `.dockerignore` excluding caches, git metadata, local envs, and runtime artifacts
- `docker-compose.yml` with a single backend service

**Step 4: Run check to verify it passes**

Run: `docker compose config`

Expected: valid rendered compose output

**Step 5: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.yml
git commit -m "feat: add docker backend deployment files"
```

### Task 3: Document Docker deployment and CLI changes

**Files:**
- Modify: `README.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`

**Step 1: Write the failing check**

Identify missing docs:

- README has no Docker deployment section
- CLI docs do not mention `serve-api` or `start --host/--port`
- config docs do not explain Docker volume expectations
- changelog has no entry for Docker deployment support

**Step 2: Run check to verify current state fails**

Run: `rg -n "Docker|serve-api|--host|--port" README.md docs/modules/cli.md docs/modules/config.md docs/changelog.md`

Expected: incomplete or missing matches

**Step 3: Write minimal implementation**

Document:

- quick-start Docker usage in `README.md`
- new CLI command/flags in `docs/modules/cli.md`
- volume and path expectations in `docs/modules/config.md`
- milestone note in `docs/changelog.md`

**Step 4: Run check to verify it passes**

Run: `rg -n "Docker|serve-api|--host|--port" README.md docs/modules/cli.md docs/modules/config.md docs/changelog.md`

Expected: all required references exist

**Step 5: Commit**

```bash
git add README.md docs/modules/cli.md docs/modules/config.md docs/changelog.md
git commit -m "docs: add docker deployment instructions"
```

### Task 4: Verify the integrated result

**Files:**
- Verify: `tests/test_cli.py`
- Verify: `Dockerfile`
- Verify: `docker-compose.yml`
- Verify: `README.md`

**Step 1: Run targeted tests**

Run: `pytest tests/test_cli.py -v`

Expected: PASS

**Step 2: Run lint on touched Python file**

Run: `ruff check src/openbiliclaw/cli.py tests/test_cli.py`

Expected: PASS

**Step 3: Validate compose file**

Run: `docker compose config`

Expected: PASS

**Step 4: Optionally build image if Docker is available**

Run: `docker compose build`

Expected: image build completes successfully

**Step 5: Commit**

```bash
git add .
git commit -m "feat: add docker one-command backend deployment"
```
