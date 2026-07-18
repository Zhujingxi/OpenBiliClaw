# Init Pool Backfill Design

**Date:** 2026-03-15

## Goal

Make `openbiliclaw init` leave the user with a materially populated discovery pool on first run, instead of stopping after a single `discover(limit=30)` pass that can still produce an empty or too-thin recommendation experience.

## Current Problem

The existing `init` flow does this:

1. pull 200 history items
2. analyze events
3. build the initial profile
4. run one `discover(limit=30)`

That last step is too weak for first-run UX:

- one strategy batch may not yield enough fresh pool items
- recommendations can still be empty right after init
- the runtime refresh controller already has staged replenishment logic, but `init` does not reuse that idea

## Constraints

- Keep the current conservative discovery concurrency defaults
- Do not change the default provider selection behavior
- Keep `init` as the single user-facing onboarding command
- Avoid introducing background jobs or new CLI flags for this change
- Preserve partial-success behavior if discovery fails after the profile is generated

## Approaches Considered

### 1. Single larger `discover(limit=50)`

Simple, but weak. A bigger limit does not guarantee the pool actually reaches 50 usable items, and it does not control which strategies are prioritized.

### 2. Reuse staged replenishment logic from runtime refresh

Run `search + related_chain`, then `trending`, then `explore`, checking the pool count after each stage and stopping once the target is reached.

This is the recommended approach because it matches existing runtime behavior, is easy to reason about, and prioritizes higher-signal sources first.

### 3. Force a full all-strategy discover regardless of pool size

This would be easy to express, but it wastes time once the pool is already sufficiently filled and makes first-run latency worse than necessary.

## Chosen Design

Extend CLI `init` so the discovery phase becomes a staged backfill loop:

1. read the current fresh pool count from the runtime database
2. if the count is already at or above 50, skip extra discovery
3. otherwise run staged discovery in this order:
   - `["search", "related_chain"]`
   - `["trending"]`
   - `["explore"]`
4. before each stage, recompute the current pool count
5. stop early once the pool count reaches 50
6. for each stage, request `limit=max(30, 50-current_pool_count)`
7. report the total number of discovered items returned across all executed stages

## Error Handling

- If any discovery stage raises, `init` still reports partial success:
  - history already ingested
  - profile already generated
  - discovery failed or only partially completed
- No rollback is added. The current project already treats discovery as additive cache/pool work.

## Testing

Add CLI tests covering:

- staged discovery runs in the expected strategy order when pool is below target
- staged discovery stops early once pool reaches 50
- the discovery limit passed to each stage uses the remaining pool deficit with a minimum of 30
- existing partial-success behavior is preserved

## Files

- Modify: `src/openbiliclaw/cli.py`
- Modify: `tests/test_cli.py`
- Update docs:
  - `README.md`
  - `docs/modules/cli.md`
  - `docs/modules/config.md`
  - `docs/changelog.md`
