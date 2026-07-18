# M2.2 LLM Registry Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build automatic LLM registry initialization, provider fallback, and a standalone provider health-check command.

**Architecture:** Keep provider implementations unchanged and layer orchestration into a small registry factory module. The registry will own provider selection, sequential fallback, and health-check reporting, while the CLI will expose read-only views into registry state plus a dedicated health-check command.

**Tech Stack:** Python 3.11+, dataclasses, pytest, Typer

---

### Task 1: Add failing registry tests

**Files:**
- Create: `tests/test_llm_registry.py`
- Test: `tests/test_llm_registry.py`

**Step 1: Write the failing test**

Add tests for:
- auto-registration from config
- default provider downgrade when configured default is unavailable
- build failure when no providers are available
- fallback on retryable provider errors
- no fallback on response-shape errors
- health check result aggregation

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_registry.py -q
```

Expected: FAIL because registry factory and fallback behavior do not exist yet

**Step 3: Write minimal implementation**

Implement only the registry factory and fallback behavior required by the tests.

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_llm_registry.py -q
```

Expected: PASS

### Task 2: Add CLI failing tests for config-show and health-check

**Files:**
- Modify: `tests/test_cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing CLI assertions**

Check:
- `config-show` prints registered provider list and active default provider
- `health-check` prints provider statuses without crashing on individual failures

**Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: FAIL until CLI uses registry information

**Step 3: Implement minimal CLI wiring**

Use the registry factory from CLI commands without changing existing command semantics.

**Step 4: Re-run CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: PASS

### Task 3: Run full project verification

**Files:**
- Modify: `src/openbiliclaw/llm/base.py`
- Modify: `src/openbiliclaw/llm/__init__.py`
- Create: `src/openbiliclaw/llm/registry.py`
- Modify: `src/openbiliclaw/cli.py`
- Test: full local gate

**Step 1: Run the full quality gate**

Run:

```bash
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m mypy src/
.venv/bin/python -m pytest -q
```

Expected: all commands pass

**Step 2: Commit**

```bash
git add src/openbiliclaw/llm/base.py src/openbiliclaw/llm/registry.py src/openbiliclaw/llm/__init__.py src/openbiliclaw/cli.py tests/test_llm_registry.py tests/test_cli.py docs/plans/2026-03-08-m22-llm-registry-design.md docs/plans/2026-03-08-m22-llm-registry.md
git commit -m "feat: add llm registry fallback and health checks"
```
