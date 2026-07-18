# Init Backfill Progress Design

**Date:** 2026-03-15

## Goal

Make `openbiliclaw init` show explicit progress while its staged first-run backfill is working, so users can distinguish "still replenishing" from "hung", especially in Docker CLI sessions.

## Problem

`init` now replenishes the fresh discovery pool up to 50 candidates in stages, but the CLI still only shows a single `4/4 发现内容` heading until the whole backfill ends. On a real networked run this can look stalled.

## Chosen Design

Keep the current staged plan and add lightweight stage progress output:

1. before each stage, print the stage index, strategy group, current pool count, target pool count, and request limit
2. after each stage, print the updated pool count and how many discovered items came back
3. if the pool already meets target, stop silently after the existing section title

Proposed stage order remains:

- `search + related_chain`
- `trending`
- `explore`

## Why This Approach

- no new flags or config
- keeps the init surface area stable
- gives immediate user-visible feedback during long-running Docker CLI flows
- easy to test with existing CLI runner tests

## Testing

Add CLI tests asserting that:

- staged init output includes the stage label and current pool progress
- stage completion output includes the updated pool count
- early-stop behavior still works

## Files

- Modify: `src/openbiliclaw/cli.py`
- Modify: `tests/test_cli.py`
- Update docs:
  - `README.md`
  - `docs/modules/cli.md`
  - `docs/changelog.md`
