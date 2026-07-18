# Interactive Init Onboarding Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend `openbiliclaw init` so Docker users can complete LLM config and Bilibili auth interactively from the CLI before the existing initialization flow continues.

**Architecture:** Keep the current `init` pipeline intact, but add a preflight onboarding phase that runs only when runtime config/auth is incomplete and the process is attached to an interactive terminal. Persist LLM settings to the runtime `config.toml`, persist cookie via the existing `AuthManager`, then resume the current history/profile/discovery flow.

**Tech Stack:** Python 3.11, Typer, TOML config loading, existing Bilibili auth manager, Pytest

---

### Task 1: Add failing tests for interactive init onboarding

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Write the failing tests**

Add tests that cover:

- `init` prompts for provider + API key when runtime config is incomplete and stdin/stdout are interactive
- `init` prompts for cookie when auth is missing and continues after validation succeeds
- `init` still exits with a clear error when config/auth is incomplete but stdin is non-interactive

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -k "interactive_init or init_reports_clear_config_error" -v`

Expected: FAIL because `init` currently exits immediately on missing config/auth.

**Step 3: Write minimal implementation**

In `src/openbiliclaw/cli.py`:

- Add helper(s) to detect interactive TTY
- Add helper(s) to prompt for provider/API key and persist config
- Add helper(s) to prompt for cookie and persist via `AuthManager`
- Call the onboarding helper at the top of `init`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -k "interactive_init or init_reports_clear_config_error" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_cli.py src/openbiliclaw/cli.py
git commit -m "feat: add interactive init onboarding"
```

### Task 2: Add config persistence support for guided init

**Files:**
- Modify: `src/openbiliclaw/config.py`
- Modify: `tests/test_config.py`

**Step 1: Write the failing tests**

Add tests that cover:

- updating default provider in the active `config.toml`
- writing API keys/models without breaking existing config loading
- preserving existing config when only one provider section is updated

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -k "update_runtime_config" -v`

Expected: FAIL because no write helper exists yet.

**Step 3: Write minimal implementation**

In `src/openbiliclaw/config.py`:

- add a small write helper for the active runtime config file
- support updating provider + API key fields required by guided init

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -k "update_runtime_config" -v`

Expected: PASS

**Step 5: Commit**

```bash
git add src/openbiliclaw/config.py tests/test_config.py
git commit -m "feat: support runtime config updates for init onboarding"
```

### Task 3: Update Docker-facing docs for one-command init

**Files:**
- Modify: `README.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`

**Step 1: Write the failing check**

Identify missing docs:

- README still describes manual config copy/edit flow
- CLI docs do not explain that `init` can guide missing config/auth
- config docs do not mention interactive onboarding in Docker mode

**Step 2: Run check to verify current state fails**

Run: `rg -n "交互|引导|docker exec -it openbiliclaw-backend openbiliclaw init" README.md docs/modules/cli.md docs/modules/config.md docs/changelog.md`

Expected: incomplete matches

**Step 3: Write minimal implementation**

Document:

- `docker exec -it openbiliclaw-backend openbiliclaw init` as the primary onboarding flow
- interactive prompts for provider/API key/cookie
- non-interactive environments still require pre-seeded config

**Step 4: Run check to verify it passes**

Run: `rg -n "交互|引导|docker exec -it openbiliclaw-backend openbiliclaw init" README.md docs/modules/cli.md docs/modules/config.md docs/changelog.md`

Expected: all required references exist

**Step 5: Commit**

```bash
git add README.md docs/modules/cli.md docs/modules/config.md docs/changelog.md
git commit -m "docs: add interactive init onboarding guide"
```

### Task 4: Verify integrated behavior

**Files:**
- Verify: `src/openbiliclaw/cli.py`
- Verify: `src/openbiliclaw/config.py`
- Verify: `tests/test_cli.py`
- Verify: `tests/test_config.py`

**Step 1: Run targeted tests**

Run: `pytest tests/test_cli.py tests/test_config.py -v`

Expected: PASS

**Step 2: Run lint**

Run: `ruff check src/openbiliclaw/cli.py src/openbiliclaw/config.py tests/test_cli.py tests/test_config.py`

Expected: PASS

**Step 3: Run Docker smoke checks**

Run: `docker exec -it openbiliclaw-backend openbiliclaw init`

Expected: if config/auth are missing and terminal is interactive, the CLI guides the user instead of exiting immediately.

**Step 4: Commit**

```bash
git add .
git commit -m "feat: guide docker users through interactive init"
```
