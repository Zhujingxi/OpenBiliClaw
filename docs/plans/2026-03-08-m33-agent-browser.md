# M3.3 Agent Browser Integration Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Integrate the official `agent-browser` CLI for availability checks, basic page navigation, text extraction, and manual verification through CLI commands.

**Architecture:** Keep `BilibiliBrowser` as a thin wrapper around the external CLI, but update it to match the real official command flow. Add a small CLI command group for browser status and manual content checks so the integration is visible and testable without wiring it into higher-level engines yet.

**Tech Stack:** Python 3.11+, asyncio subprocess, Typer, pytest

---

### Task 1: Add failing tests for browser availability, install guidance, and command flow

**Files:**
- Create: `tests/test_bilibili_browser.py`
- Modify: `src/openbiliclaw/bilibili/browser.py`

**Step 1: Write the failing tests**

Cover:
- executable discovery and availability checks
- installation hint text
- `navigate()` calling the official CLI shape
- `get_page_content()` using the `open + snapshot` flow
- non-zero subprocess results returning clear errors

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_bilibili_browser.py -q
```

Expected: FAIL because the current browser wrapper assumes outdated CLI behavior

**Step 3: Write minimal implementation**

Implement:
- accurate installation hint helper
- command execution updates
- snapshot-based page text extraction

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_bilibili_browser.py -q
```

Expected: PASS

### Task 2: Add failing CLI tests for browser commands

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Write the failing tests**

Cover:
- `browser status` shows available / unavailable states
- `browser open <url>` delegates to the browser wrapper
- `browser content <url>` prints extracted page text

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: FAIL because the browser command group does not exist yet

**Step 3: Write minimal implementation**

Add:
- a Typer `browser` command group
- `browser status`
- `browser open`
- `browser content`

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: PASS

### Task 3: Run the full quality gate

**Files:**
- Modify: `src/openbiliclaw/bilibili/browser.py`
- Modify: `src/openbiliclaw/cli.py`
- Create: `tests/test_bilibili_browser.py`
- Modify: `tests/test_cli.py`
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
git add src/openbiliclaw/bilibili/browser.py src/openbiliclaw/cli.py tests/test_bilibili_browser.py tests/test_cli.py docs/plans/2026-03-08-m33-agent-browser-design.md docs/plans/2026-03-08-m33-agent-browser.md
git commit -m "feat: integrate agent browser cli"
```
