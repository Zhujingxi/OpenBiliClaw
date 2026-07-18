# M1.2 Config System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete task `1.2 配置系统完善` with automatic template generation, config diagnostics, and strict validation hooks.

**Architecture:** Keep `load_config()` permissive and introduce a separate diagnostics/validation layer in `src/openbiliclaw/config.py`. The CLI will use permissive diagnostics for `config-show` and strict validation for runtime commands that need provider credentials.

**Tech Stack:** Python 3.11+, dataclasses, pathlib, pytest, Typer

---

### Task 1: Add failing tests for config initialization and diagnostics

**Files:**
- Modify: `tests/test_config.py`
- Create: `tests/test_cli.py`
- Test: `tests/test_config.py`, `tests/test_cli.py`

**Step 1: Write the failing test**

Add tests for:
- missing `config.toml` auto-creates from `config.example.toml`
- diagnostics report missing default provider API key
- strict validation raises a clear config error
- `config-show` succeeds and prints guidance when config is auto-generated

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_cli.py -q
```

Expected: FAIL because the new diagnostics and CLI behavior do not exist yet

**Step 3: Write minimal implementation**

Add only the config diagnostics, template creation, and CLI output needed to satisfy the tests.

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_cli.py -q
```

Expected: PASS

### Task 2: Implement config diagnostics and strict validation

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Test: `tests/test_config.py`

**Step 1: Add the failing validation assertions**

Test these behaviors:
- invalid `bilibili.auth_method` is rejected
- `openai` / `claude` / `deepseek` default provider requires `api_key`
- `ollama` does not require `api_key`

**Step 2: Run narrow tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py -q
```

Expected: FAIL on new validation expectations

**Step 3: Implement the smallest validation surface**

Add:
- a `ConfigIssue` / diagnostics structure
- an auto-init helper for `config.toml`
- a `ConfigError` exception
- a strict validation function for runtime commands

**Step 4: Re-run config tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py -q
```

Expected: PASS

### Task 3: Wire CLI behavior and refresh documentation

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Modify: `config.example.toml`
- Test: `tests/test_cli.py`

**Step 1: Add the failing CLI assertions**

Check:
- `config-show` prints current config plus guidance messages
- runtime commands surface clear config errors when required sensitive fields are missing

**Step 2: Run CLI tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: FAIL until CLI uses diagnostics and strict validation

**Step 3: Implement the minimal CLI wiring**

Use diagnostics for `config-show` and strict validation for runtime commands most likely to require LLM access.

**Step 4: Re-run targeted tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: PASS

### Task 4: Run full project verification

**Files:**
- Modify: none beyond previous tasks
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
git add src/openbiliclaw/config.py src/openbiliclaw/cli.py tests/test_config.py tests/test_cli.py config.example.toml docs/plans/2026-03-08-m12-config-system-design.md docs/plans/2026-03-08-m12-config-system.md
git commit -m "feat: improve config initialization and validation"
```
