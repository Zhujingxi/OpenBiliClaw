# Bootstrap Service Checks Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Block one-line install auto-init until the configured LLM provider and embedding service pass real lightweight checks.

**Architecture:** Add a bootstrap-local pre-init service gate in `scripts/agent_bootstrap.py`. The gate runs an installed-project Python probe, parses structured JSON, emits `BOOTSTRAP_STATUS`, and only calls `openbiliclaw init` when both required services pass.

**Tech Stack:** Python stdlib subprocess/JSON, OpenBiliClaw config + LLM registry + embedding service, pytest.

---

### Task 1: Add Service Check Gate Tests

**Files:**
- Modify: `tests/test_agent_bootstrap.py`

**Step 1: Write failing tests**

Add tests that assert:

- `run_pre_init_service_checks(...)` returns success when the probe reports LLM and embedding available.
- LLM failure returns `available=False` and lists `llm` as failing.
- Embedding failure returns `available=False` and lists `embedding` as failing.
- Explicitly disabled embedding is accepted as skipped.

Use a fake `runner` callable so tests do not call real providers.

**Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_agent_bootstrap.py::test_pre_init_service_checks_pass_when_probe_reports_services_ready tests/test_agent_bootstrap.py::test_pre_init_service_checks_fail_when_llm_probe_fails tests/test_agent_bootstrap.py::test_pre_init_service_checks_fail_when_embedding_probe_fails tests/test_agent_bootstrap.py::test_pre_init_service_checks_accept_disabled_embedding -q
```

Expected: FAIL because `run_pre_init_service_checks` does not exist.

### Task 2: Implement Bootstrap Probe

**Files:**
- Modify: `scripts/agent_bootstrap.py`

**Step 1: Add minimal implementation**

Add:

- `build_service_check_command(project_dir, mode)`
- `run_pre_init_service_checks(project_dir, mode, runner=run_capture)`
- helper parsing for probe JSON.

The local command should run `uv run python -c <probe>` when `uv` is present,
falling back to `sys.executable -c <probe>`. Docker mode should run
`docker exec -i openbiliclaw-backend python -c <probe>` so it checks the same
runtime config copied into the container.

The probe should:

- load config;
- build the LLM registry;
- call `complete_provider(default_provider, ...)`;
- build embedding service;
- if embedding provider is empty, mark it skipped;
- otherwise call `embed("openbiliclaw bootstrap embedding check")` and require a non-empty vector.

**Step 2: Run focused tests**

Run the four tests from Task 1. Expected: PASS.

### Task 3: Wire Gate Before Init

**Files:**
- Modify: `scripts/agent_bootstrap.py`

**Step 1: Add failing flow test**

Add a test that monkeypatches `run_pre_init_service_checks` to fail and asserts
the bootstrap would emit `service_check_failed` instead of running init.

**Step 2: Implement gate**

Immediately before `run_init_streaming(...)`, call `run_pre_init_service_checks`.
If unavailable, emit:

```json
{"status":"service_check_failed","message":"pre_init_service_check_failed"}
```

and return `0` so the backend stays running and the installer summary can guide the user.

**Step 3: Run focused tests**

Run:

```bash
pytest tests/test_agent_bootstrap.py -q
```

Expected: PASS.

### Task 4: Update Install Docs

**Files:**
- Modify: `docs/agent-install.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/changelog.md`

**Step 1: Update docs**

Document that one-line install blocks auto-init if LLM or embedding checks fail,
and the user must repair the provider/key/Ollama/model before rerunning the
printed bootstrap command.

**Step 2: Run doc contract tests**

Run:

```bash
pytest tests/test_install_contract_docs.py -q
```

Expected: PASS.

### Task 5: Final Verification

Run:

```bash
ruff check scripts/agent_bootstrap.py tests/test_agent_bootstrap.py
pytest tests/test_agent_bootstrap.py tests/test_install_contract_docs.py -q
```

Expected: PASS.
