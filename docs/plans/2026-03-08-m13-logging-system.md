# M1.3 Logging System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a reusable logging setup with Rich console output, file logging, and configurable log levels.

**Architecture:** Keep logging concerns out of `cli.py` by adding a dedicated setup module and a small `LoggingConfig` dataclass. The CLI callback will load config once, initialize logging, and allow a command-line override for console verbosity.

**Tech Stack:** Python 3.11+, logging, Rich, pathlib, pytest, Typer

---

### Task 1: Add failing tests for logging config and setup

**Files:**
- Modify: `tests/test_config.py`
- Create: `tests/test_logging_setup.py`
- Test: `tests/test_config.py`, `tests/test_logging_setup.py`

**Step 1: Write the failing test**

Add tests for:
- `[logging]` config values parsing correctly
- logging setup creating the target directory and log file
- repeated setup not duplicating handlers

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_logging_setup.py -q
```

Expected: FAIL because logging config and setup module do not exist yet

**Step 3: Write minimal implementation**

Add only the logging config dataclass and logging setup code needed for the tests.

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_logging_setup.py -q
```

Expected: PASS

### Task 2: Add CLI wiring and override behavior

**Files:**
- Modify: `src/openbiliclaw/cli.py`
- Create: `tests/test_cli_logging.py`
- Test: `tests/test_cli_logging.py`

**Step 1: Write the failing CLI test**

Check:
- a global `--log-level` option exists
- CLI initializes logging before command execution
- command-line level overrides config level

**Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli_logging.py -q
```

Expected: FAIL until CLI callback initializes logging

**Step 3: Implement minimal CLI wiring**

Use a Typer callback to initialize logging once per command invocation.

**Step 4: Re-run CLI logging tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli_logging.py -q
```

Expected: PASS

### Task 3: Refresh config example and run full verification

**Files:**
- Modify: `config.example.toml`
- Modify: `src/openbiliclaw/config.py`
- Test: full project gate

**Step 1: Update the config example**

Document:
- `level`
- `file_level`
- `directory`
- `filename`

**Step 2: Run full verification**

Run:

```bash
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m mypy src/
.venv/bin/python -m pytest -q
```

Expected: all commands pass

**Step 3: Commit**

```bash
git add src/openbiliclaw/config.py src/openbiliclaw/cli.py src/openbiliclaw/logging_setup.py config.example.toml tests/test_config.py tests/test_logging_setup.py tests/test_cli_logging.py docs/plans/2026-03-08-m13-logging-system-design.md docs/plans/2026-03-08-m13-logging-system.md
git commit -m "feat: add structured logging setup"
```
