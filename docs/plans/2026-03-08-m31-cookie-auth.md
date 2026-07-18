# M3.1 Cookie Auth Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add validated Bilibili cookie authentication with local persistence and CLI commands for login and status checks.

**Architecture:** Keep request logic in `BilibiliAPIClient` and move authentication state management into `AuthManager`. The CLI will become the only interactive layer, while auth persistence and nav validation stay reusable for later init/history flows.

**Tech Stack:** Python 3.11+, httpx, Typer, pytest, Rich

---

### Task 1: Add failing tests for AuthManager and nav validation

**Files:**
- Create: `tests/test_bilibili_auth.py`
- Modify: `tests/test_bilibili_api.py`
- Modify: `src/openbiliclaw/bilibili/auth.py`
- Modify: `src/openbiliclaw/bilibili/api.py`

**Step 1: Write the failing tests**

Cover:
- cookie persistence and reload
- validation success returns nickname and UID
- validation failure returns a clear error
- `get_nav_info()` parses the nav payload correctly

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_bilibili_auth.py tests/test_bilibili_api.py -q
```

Expected: FAIL because nav validation and structured auth status do not exist yet

**Step 3: Write minimal implementation**

Implement:
- structured auth status/result models
- `BilibiliAPIClient.get_nav_info()`
- `AuthManager.validate_cookie()` / `get_status()`

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_bilibili_auth.py tests/test_bilibili_api.py -q
```

Expected: PASS

### Task 2: Add failing CLI tests for auth login and auth status

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Write the failing tests**

Cover:
- `auth login` accepts interactive cookie input and saves on success
- `auth login --cookie ...` does not save on validation failure
- `auth status` reports missing cookie clearly
- `auth status` reports authenticated nickname and UID

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: FAIL because auth subcommands do not exist yet

**Step 3: Write minimal implementation**

Add:
- Typer auth sub-app or command group
- `auth login`
- `auth status`

**Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: PASS

### Task 3: Run the full project quality gate

**Files:**
- Modify: `src/openbiliclaw/bilibili/auth.py`
- Modify: `src/openbiliclaw/bilibili/api.py`
- Modify: `src/openbiliclaw/cli.py`
- Create: `tests/test_bilibili_auth.py`
- Create or Modify: `tests/test_bilibili_api.py`
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
git add src/openbiliclaw/bilibili/auth.py src/openbiliclaw/bilibili/api.py src/openbiliclaw/cli.py tests/test_bilibili_auth.py tests/test_bilibili_api.py tests/test_cli.py docs/plans/2026-03-08-m31-cookie-auth-design.md docs/plans/2026-03-08-m31-cookie-auth.md
git commit -m "feat: add bilibili cookie auth commands"
```
