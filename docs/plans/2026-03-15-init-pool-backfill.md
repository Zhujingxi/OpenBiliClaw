# Init Pool Backfill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `openbiliclaw init` replenish the discovery pool in stages until at least 50 fresh candidates are available for first-run recommendations.

**Architecture:** Reuse the runtime refresh controller's staged strategy order directly in CLI `init`. Add a small helper in `cli.py` that inspects the runtime database, runs staged discovery with conservative limits, and stops once the pool target is met or the strategy plan is exhausted.

**Tech Stack:** Python, Typer CLI, existing discovery engine and SQLite-backed runtime database, pytest

---

### Task 1: Lock staged init behavior with failing tests

**Files:**
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

Add tests that assert:

```python
result = runner.invoke(app, ["init"])
assert fake_discovery.calls == [
    (["search", "related_chain"], 50),
    (["trending"], 30),
]
```

and:

```python
assert fake_discovery.calls == [(["search", "related_chain"], 30)]
```

for the early-stop case.

**Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -k "init and backfill" -v`

Expected: FAIL because `init` still performs one plain `discover(limit=30)`.

**Step 3: Write minimal implementation**

No implementation in this task.

**Step 4: Run test to verify it still fails for the expected reason**

Run the same targeted pytest command and confirm the failure is about missing staged behavior.

**Step 5: Commit**

Do not commit yet.

### Task 2: Implement staged init pool backfill

**Files:**
- Modify: `src/openbiliclaw/cli.py`

**Step 1: Write the helper**

Add a helper that:

```python
def _run_init_discovery_backfill(profile: Any, target_pool_count: int = 50) -> int:
    ...
```

Responsibilities:

- read `database.count_pool_candidates()`
- execute stages in order
- call `discovery_engine.discover(profile, strategies=stage, limit=max(30, target_pool_count-current_pool_count))`
- stop when the pool reaches target
- return total discovered item count

**Step 2: Run focused tests**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -k "init and backfill" -v`

Expected: PASS

**Step 3: Refactor init to use the helper**

Replace the single `discover(limit=30)` call in `init()` with the helper while preserving partial-success handling.

**Step 4: Run broader CLI tests**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -v`

Expected: PASS

**Step 5: Commit**

Do not commit yet.

### Task 3: Update docs for first-run refill behavior

**Files:**
- Modify: `README.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/changelog.md`

**Step 1: Document the new init behavior**

Add short notes that `init` now attempts staged pool replenishment up to 50 fresh candidates on first run, prioritizing `search + related_chain`, then `trending`, then `explore`.

**Step 2: Run docs-adjacent verification**

Run: `./.venv/bin/ruff check src/openbiliclaw/cli.py tests/test_cli.py`

Expected: PASS

**Step 3: Commit**

Do not commit yet.

### Task 4: Final verification

**Files:**
- Modify: none

**Step 1: Run focused full verification**

Run:

```bash
./.venv/bin/python -m pytest tests/test_cli.py tests/test_config.py tests/test_docker_runtime.py -v
./.venv/bin/ruff check src/openbiliclaw/cli.py tests/test_cli.py tests/test_config.py tests/test_docker_runtime.py
```

Expected: PASS

**Step 2: Run diff hygiene**

Run: `git diff --check -- src/openbiliclaw/cli.py tests/test_cli.py README.md docs/modules/cli.md docs/modules/config.md docs/changelog.md docs/plans/2026-03-15-init-pool-backfill-design.md docs/plans/2026-03-15-init-pool-backfill.md`

Expected: PASS

**Step 3: Commit**

```bash
git add src/openbiliclaw/cli.py tests/test_cli.py README.md docs/modules/cli.md docs/modules/config.md docs/changelog.md docs/plans/2026-03-15-init-pool-backfill-design.md docs/plans/2026-03-15-init-pool-backfill.md
git commit -m "feat: backfill init discovery pool"
```
