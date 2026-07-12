# Final Review Fix Report — Dynamic Source-Deficit Recovery

## Status

DONE

Base: `29ae646b`

## Finding fixed

Suppressed-inventory recovery computed its source-deficit ordering once before
the restore loop. With target 2, quotas `bilibili=1` / `zhihu=1`, suppressed
Bilibili rows at `.99` and `.98`, and a suppressed Zhihu row at `.70`, the
static order restored Bilibili twice. The second selection did not observe that
Bilibili had reached its quota while Zhihu still had a deficit.

## Root cause and fix

Recovery already runs in one `BEGIN IMMEDIATE` transaction and reloads the
canonical available rows after every update to enforce the target cap. The
family counts used by the sort key, however, remained the pre-recovery counts.

The recovery loop now selects the best remaining eligible row from the current
family counts, restores it, reloads the same canonical availability predicate,
and rebuilds family counts before selecting again. This keeps source quotas as
an ordering preference only: if no under-quota family has an eligible row, the
highest-score over-quota row can still fill the global availability gap.

The transaction, canonical readiness predicate, target stopping condition,
protected recovered IDs, net `recovered_suppressed` accounting, and rollback
behavior are unchanged.

## TDD evidence

### RED

Added `test_recover_suppressed_rebalances_source_deficit_after_each_restore`:

```text
PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python \
  -m pytest tests/test_pool_maintenance.py -q \
  -k 'rebalances_source_deficit_after_each_restore'
```

Before the implementation it failed exactly as expected: `BV_RECOVER_98` was
`fresh`, not `suppressed`, proving the static initial ranking restored B+B.

### GREEN

```text
PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python \
  -m pytest tests/test_pool_maintenance.py -q -k 'recover_suppressed'
# 4 passed, 7 deselected

PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python \
  -m pytest tests/test_pool_maintenance.py tests/test_storage.py \
  tests/test_refresh_runtime.py -q
# 275 passed

PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python \
  -m pytest -q
# 4287 passed, 17 skipped (existing framework deprecation warnings)

/Users/white/workspace/OpenBiliClaw/.venv/bin/ruff format --check \
  src/openbiliclaw/storage/database.py tests/test_pool_maintenance.py
/Users/white/workspace/OpenBiliClaw/.venv/bin/ruff check src/ tests/
PYTHONPATH="$PWD/src" /Users/white/workspace/OpenBiliClaw/.venv/bin/python -m mypy src/
git diff --check
# all passed; MyPy: Success: no issues found in 189 source files
```

## Documentation scope

No module doc or changelog update was needed: the existing storage contract
already states that recovery ranks by the current source-family deficit and
reloads canonical availability after each restored row. This fix makes that
documented behavior true for multi-row recovery.

## Scope guard

No real-provider/live integration or Task 9 files were changed. No Soul,
prompt, token, cost, or LLM-concurrency code was changed.
